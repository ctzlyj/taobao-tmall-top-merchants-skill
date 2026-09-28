import argparse
import os
import subprocess
import sys
from pathlib import Path

from configure_enterprise_keys import FENGNIAO_ENV_NAME, read_user_environment


def execute_fengniao(arguments, skill_dir=None, key=None, runner=None):
    path = Path(skill_dir or Path.home() / ".openclaw/skills/company-search-fengniao")
    tool = path / "scripts/tool.mjs"
    token = key or read_user_environment(FENGNIAO_ENV_NAME)
    if not tool.is_file():
        return {"returncode": 2, "stdout": "", "stderr": "风鸟 Skill 未安装"}
    if not token:
        return {"returncode": 2, "stdout": "", "stderr": "风鸟 Key 未配置"}
    environment = os.environ.copy()
    environment[FENGNIAO_ENV_NAME] = token
    execute = runner or subprocess.run
    try:
        completed = execute(
            ["node", str(tool), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=environment,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        return {"returncode": 2, "stdout": "", "stderr": "风鸟查询超时，保留检查点后再恢复"}
    except OSError:
        return {"returncode": 2, "stdout": "", "stderr": "无法启动风鸟 Skill"}
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout.replace(token, "[REDACTED]"),
        "stderr": completed.stderr.replace(token, "[REDACTED]"),
    }


def main():
    parser = argparse.ArgumentParser(description="使用当前用户安全配置运行风鸟 Skill")
    parser.add_argument("--skill-dir")
    parser.add_argument("--allow-paid-api", action="store_true")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.allow_paid_api:
        parser.error("PAID_API_NOT_AUTHORIZED: --allow-paid-api is required before provider arguments")
    result = execute_fengniao(args.arguments, args.skill_dir)
    if result["stdout"]:
        print(result["stdout"], end="" if result["stdout"].endswith("\n") else "\n")
    if result["stderr"]:
        print(result["stderr"], file=sys.stderr)
    raise SystemExit(result["returncode"])


if __name__ == "__main__":
    main()
