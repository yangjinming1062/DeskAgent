import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, make_url, pool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import modules
import modules.media.models  # noqa: F401 — video_gen_jobs is intentionally not imported by modules/__init__
from common.model import ModelBase
from components.config import SETTINGS

config = context.config
# fileConfig 默认 disable_existing_loggers=True 且会用 alembic.ini 的
# [logger_root]（WARNING + stderr）整个替换 root —— 应用内启动迁移
# （main.py lifespan 已先 setup_logging）绝不能走这条路径，否则迁移
# 一跑完整个 web 进程就"失明"（无 access log、无应用日志）。仅 CLI
# 调用 alembic 时才配置日志。
if config.config_file_name and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# URL 优先级：调用方显式注入（main.py 启动迁移）> DATABASE_URL 环境变量 > SETTINGS。
if not config.get_main_option("sqlalchemy.url"):
    url = make_url(os.environ.get("DATABASE_URL") or SETTINGS.database_url)
    url = url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))

target_metadata = ModelBase.metadata


def _include_object(obj, name, type_, reflected, compare_to):
    # 迁移管理的索引（partial unique / hnsw / gin trgm）只存在于迁移文件、
    # 不在模型 metadata 中（声明进模型会让 SQLite create_all 丢失 WHERE 语义
    # 变成全量唯一索引）。跳过"仅存在于数据库"的索引，autogenerate 才不会
    # 提议删除它们。代价：从模型中删除 Index 时 autogenerate 不会生成对应
    # drop_index，需手写。
    if type_ == "index" and reflected and compare_to is None:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True, compare_server_default=True, include_object=_include_object)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
