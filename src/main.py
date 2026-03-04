"""
BUAA 博雅课程自动推送智能体 - 入口文件
启动 APScheduler 定时任务 + Flask Web 控制台
"""

import os
import sys

# 确保项目根目录在 sys.path 中，解决 `from src.xxx import` 路径问题
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import asyncio
import threading
from loguru import logger
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()
# 也尝试从 config 目录加载
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "config", ".env"))

# 配置日志
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:8}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
    level="INFO",
)
logger.add(
    "logs/boya_agent_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    retention="30 days",
    level="DEBUG",
)

from src.models import init_db
from src.scheduler import start_scheduler, run_scrape_task
from web.app import app


def run_flask():
    """在子线程中运行 Flask"""
    host = os.getenv("WEB_HOST", "0.0.0.0")
    port = int(os.getenv("WEB_PORT", "5000"))
    logger.info(f"Web 控制台启动: http://{host}:{port}")
    app.run(host=host, port=port, debug=False, use_reloader=False)


async def main():
    """主入口"""
    logger.info("=" * 60)
    logger.info("  BUAA 博雅课程自动推送智能体 启动")
    logger.info("=" * 60)

    # 初始化数据库
    init_db()
    logger.info("数据库初始化完成")

    # 获取调度间隔
    interval = int(os.getenv("SCRAPE_INTERVAL_MINUTES", "10"))

    # 检查是否是单次运行模式
    if "--once" in sys.argv:
        logger.info("单次运行模式")
        await run_scrape_task()
        logger.info("单次运行完成")
        return

    # 启动 Flask（子线程）
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # 启动调度器
    start_scheduler(interval_minutes=interval)

    # 首次立即运行一次
    logger.info("执行首次抓取任务...")
    await run_scrape_task()

    # 保持运行
    logger.info("系统已就绪，等待定时任务触发...")
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在关闭...")
        from src.scheduler import close_browser
        await close_browser()


if __name__ == "__main__":
    asyncio.run(main())
