import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from miyouqian.cli import main


def main_handler(event, context):
    print("====== 米游签云函数任务开始 ======")
    try:
        # 配置文件路径按环境变量来；以前这里写死的是 /path/to/your/config.json，
        # 照抄的人一定会踩。云函数里请把 MIYOUQIAN_CONFIG 指到实际路径。
        config_path = os.environ.get("MIYOUQIAN_CONFIG") or "config.yaml"
        exit_code = main(["--config", config_path, "run"])
        print(f"====== 任务执行完毕，退出码: {exit_code} ======")
        return None
    except Exception as e:
        print(f"====== 任务运行异常: {str(e)} ======")
        raise e
