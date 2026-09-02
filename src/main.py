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


def _validate_runtime_security_config():
    secret = (os.getenv("WEB_SECRET_KEY") or "").strip()
    if len(secret) < 32 or secret.lower().startswith("replace-with-"):
        raise RuntimeError("WEB_SECRET_KEY 未配置、仍是示例值或长度不足 32 个字符")

    admin_token = (os.getenv("ADMIN_API_TOKEN") or "").strip()
    admin_user = (os.getenv("ADMIN_USERNAME") or "").strip()
    admin_password = os.getenv("ADMIN_PASSWORD") or ""
    if admin_token.lower().startswith("replace-with-"):
        admin_token = ""
    if admin_password.lower().startswith("replace-with-"):
        admin_password = ""
    if not admin_token and not (admin_user and admin_password):
        raise RuntimeError("必须配置 ADMIN_API_TOKEN，或同时配置 ADMIN_USERNAME/ADMIN_PASSWORD")


_validate_runtime_security_config()

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
    """在子线程中运行单进程 WSGI Web 服务。

    调度器和 Playwright 浏览器都属于当前进程的全局资源，不能直接使用
    多 worker WSGI 部署，否则会重复启动定时任务和浏览器。Waitress 的多线程
    模式可以提高接口并发，同时保留单进程资源模型。
    """
    from waitress import serve

    host = os.getenv("WEB_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_PORT", "5000"))
    threads = max(2, min(32, int(os.getenv("WEB_THREADS", "8"))))
    logger.info(f"Web 服务启动: http://{host}:{port} (Waitress, threads={threads})")
    serve(
        app,
        host=host,
        port=port,
        threads=threads,
        connection_limit=1000,
        channel_timeout=120,
        ident="boya-agent",
    )


async def main():
    """主入口。"""
    logger.info("=" * 60)
    logger.info("BUAA 博雅课程提醒系统启动")
    logger.info("=" * 60)

    init_db()
    logger.info("数据库初始化完成")

    interval = max(1, min(1440, int(os.getenv("SCRAPE_INTERVAL_MINUTES", "10"))))

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
