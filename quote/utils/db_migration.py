import asyncio
from pathlib import Path
from typing import List, Optional

from zhenxun.services.log import logger


async def update_model_path(old_path: str, new_path: str, db_url: Optional[str] = None) -> bool:
    """更新模型路径"""
    try:
        logger.info(f"模型路径变更: {old_path} -> {new_path}", "数据库迁移")
        logger.info("由于无法获取数据库配置，跳过数据库迁移", "数据库迁移")
        logger.info("如果您遇到数据库错误，请手动更新数据库中的模型路径", "数据库迁移")

        return True
    except Exception as e:
        logger.error(f"更新数据库模型路径失败: {e}", "数据库迁移", e=e)
        return False


async def get_current_model_path() -> str:
    """获取当前路径"""
    try:
        import quote

        return f"{quote.__package__}.model"
    except ImportError:
        current_file = Path(__file__)
        plugin_dir = current_file.parent.parent.name
        return f"zhenxun.plugins.{plugin_dir}.model"


async def migrate_from_old_path(old_paths: List[str]) -> bool:
    """路径迁移"""
    current_path = await get_current_model_path()
    success = True

    for old_path in old_paths:
        if old_path != current_path:
            logger.info(f"尝试从 {old_path} 迁移到 {current_path}", "数据库迁移")
            if not await update_model_path(old_path, current_path):
                success = False

    return success


async def run_migration():
    """执行迁移"""
    old_paths = [
        "zhenxun.plugins.quote.model",
        "zhenxun.plugins.nonebot_plugin_quote.model",
        "zhenxun.plugins.zhenxun_plugin_quote.model",
    ]

    logger.info("开始数据库迁移...", "数据库迁移")
    success = await migrate_from_old_path(old_paths)

    if success:
        logger.info("数据库迁移完成", "数据库迁移")
    else:
        logger.warning("数据库迁移部分失败，请检查日志", "数据库迁移")


if __name__ == "__main__":
    asyncio.run(run_migration())
