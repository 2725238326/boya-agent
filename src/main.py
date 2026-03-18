"""
BUAA 博雅课程提醒系统入口。
负责初始化数据库、启动 Flask 与调度器，并执行首次抓取。
"""

import asyncio
import os
import sys
import threading

from dotenv import load_dotenv
from loguru import logger

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

load_dotenv()
load_dotenv(os.path.join(PROJECT_ROOT, "config", ".env"))

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
from src.scheduler import close_browser, run_scrape_task, set_runtime_loop, start_scheduler
from web.app import app


def run_flask():
    """在子线程中运行 Flask Web 服务。"""
    host = os.getenv("WEB_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_PORT", "5000"))
    logger.info(f"Web 服务启动: http://{host}:{port}")
    app.run(host=host, port=port, debug=False, use_reloader=False)


async def main():
    """主入口。"""
    logger.info("=" * 60)
    logger.info("BUAA 博雅课程提醒系统启动")
    logger.info("=" * 60)

    init_db()
    logger.info("数据库初始化完成")

    interval = int(os.getenv("SCRAPE_INTERVAL_MINUTES", "10"))

    if "--once" in sys.argv:
        logger.info("以单次运行模式执行")
        await run_scrape_task()
        logger.info("单次运行完成")
        return

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    set_runtime_loop(asyncio.get_running_loop())
    start_scheduler(interval_minutes=interval)

    logger.info("执行首次抓取任务...")
    await run_scrape_task()

    logger.info("系统已就绪，等待定时任务触发")
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在关闭浏览器资源...")
        await close_browser()


if __name__ == "__main__":
    asyncio.run(main())
