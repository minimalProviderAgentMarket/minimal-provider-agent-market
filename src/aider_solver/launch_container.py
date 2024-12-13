import os
import shlex
from docker import from_env as docker_from_env
from dotenv import load_dotenv
from loguru import logger
import re
import openai
from src.config import SETTINGS

DOCKER_IMAGE = "paulgauthier/aider"
load_dotenv()
ENV_VARS = {key: os.getenv(key) for key in os.environ.keys()}
WEAK_MODEL = "gpt-4o-mini"
openai.api_key = SETTINGS.openai_api_key

def _clean_logs(logs: str) -> str:
    anti_escape_logs = re.compile(r'\x1B[@-_][0-?]*[ -/]*[@-~]')
    logs = anti_escape_logs.sub('', logs).split("Tokens:")[0]

    prompt = """
    Below are the raw logs from an AI coding assistant. Please rewrite these logs as a clear, 
    concise message to a user, focusing on the important actions and changes made. Remove any 
    technical artifacts, ANSI escape codes, and redundant information. Format the response 
    in a user-friendly way.

    Raw logs:
    {logs}
    """
    
    try:
        response = openai.chat.completions.create(
            model=WEAK_MODEL,
            messages=[
                {"role": "system", "content": "You are a helpful assistant that processes technical logs."},
                {"role": "user", "content": prompt.format(logs=logs)}
            ],
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"Failed to process logs with GPT-4: {e}")
        return logs

def launch_container_with_repo_mounted(
    repo_directory: str, model_name: str, instance_background: str, test_command: str, timeout: int = 300
) -> str:
    docker_client = docker_from_env()
    
    escaped_background = instance_background.replace("'", "'\"'\"'")
    escaped_test_command = shlex.quote(test_command) if test_command else ""
    
    test_args_and_command = f' --test-command {escaped_test_command}' if test_command else ""
    
    entrypoint = [
        "/bin/bash",
        "-c",
        (f"source /venv/bin/activate && python modify_repo.py --model-name {shlex.quote(model_name)} "
        f"--instance-background '{escaped_background}'{test_args_and_command}")
    ]
    
    logger.info(f"Launching container with entrypoint: {entrypoint}")
    try:
        container = docker_client.containers.run(
            DOCKER_IMAGE,
            entrypoint=entrypoint,
            user=f"{os.getuid()}:{os.getgid()}",
            volumes={
                f"{repo_directory}/.": {"bind": "/app", "mode": "rw"},
                "/tmp/aider_cache": {"bind": "/home/ubuntu", "mode": "rw"},
            },
            environment=ENV_VARS,
            detach=True,
            tty=True,
            stdin_open=True,
        )
        logger.info("Container launched")

        result = container.wait(timeout=timeout)
        container.stop()

        logs = _clean_logs(container.logs(stream=False).decode("utf-8"))

        logger.info(f"Logs: {logs}")

        exit_status = result.get("StatusCode", -1)
        logger.info(f"Container finished with exit code: {exit_status}")

        container.remove()
        logger.info("Container removed")

        return logs

    except Exception as e:
        logger.error(f"Container execution failed: {e}")
        try:
            logger.info(f"Logs: {container.logs(stream=False).decode('utf-8')}")
            container.stop()
            container.remove()
        except:
            container.stop()
            container.remove()
            pass
        raise