# ─── Tier 5 — 横向基础 ──────────────────────────────────────────────────────────
from .async_jobs import auto_generate_title
from .async_jobs import create_job
from .async_jobs import get_job
from .async_jobs import list_jobs
from .async_jobs import pause_job
from .async_jobs import remove_job
from .async_jobs import resume_job
from .async_jobs import run_background_memory_review
from .async_jobs import start_scheduler
from .async_jobs import stop_scheduler
from .async_jobs import update_job
from .attachments import attachment_root
from .attachments import gc_session as attachments_gc_session
from .attachments import path_attach_ref
from .attachments import remove as attachments_remove
from .attachments import session_dir
from .auth_helpers import authenticate_ws_token
from .backend_tools import cronjob
from .backend_tools import CRONJOB_SCHEMA
from .backend_tools import IMAGE_GENERATION_SCHEMA
from .backend_tools import image_generation_tool
from .backend_tools import is_safe_outbound
from .backend_tools import SEARCH_TOOLS_SCHEMA
from .backend_tools import search_tools_tool
from .backend_tools import SEND_MESSAGE_SCHEMA
from .backend_tools import send_message_tool
from .backend_tools import text_to_speech_tool
from .backend_tools import TTS_SCHEMA
from .backend_tools import WEB_EXTRACT_SCHEMA
from .backend_tools import web_extract_tool
from .backend_tools import WEB_SEARCH_SCHEMA
from .backend_tools import web_search_tool
from .chat import AGENT_DELEGATE_SCHEMA
from .chat import agent_delegate_tool
from .chat import build_session_messages
from .chat import build_system_prompt
from .chat import build_system_prompt_parts
from .chat import CORE_TOOLS
from .chat import Emitter
from .chat import HeadlessEmitter
from .chat import IterationBudget
from .chat import load_user_settings
from .chat import run_chat_turn
from .chat import safe_emit
from .chat import StreamingThinkScrubber
from .companion import AvatarGenerationError
from .companion import build_system_prompt_extras
from .companion import generate_avatar
from .companion import get_active_avatar
from .companion import get_or_create_persona
from .companion import list_avatar_history
from .companion import PersonaValidationError
from .companion import update_persona
from .correlation import adopt_inbound
from .correlation import begin_local_scope
from .correlation import correlated_exception_response
from .correlation import correlation_id_middleware
from .correlation import new_request_id
from .correlation import normalize_inbound
from .correlation import REQUEST_ID_HEADER
from .llm import call_with_retry
from .llm import ClassifiedError
from .llm import classify_api_error
from .llm import client_for_config
from .llm import client_for_service
from .llm import client_for_user
from .llm import compress_history_if_needed
from .llm import FailoverReason
from .llm import get_async_client
from .llm import LLMRuntimeError
from .llm import MissingLlmConfigError
from .llm import resolve_service_row
from .llm import resolve_user_llm_config
from .rate_limit import limiter
from .rate_limit import rate_limit_exception_handler
from .rate_limit import stash_user_id_middleware
from .redact import mask_secret
from .redact import redact_sensitive_text
from .redact import RedactingFormatter
from .slash_commands import CommandContext
from .slash_commands import commands_catalog
from .slash_commands import exec_slash_command
from .temp_files import cleanup_expired
from .temp_files import gc_session as temp_files_gc_session
from .temp_files import get_file_path
from .temp_files import save_file
from .tools_runtime import ALWAYS_AVAILABLE
from .tools_runtime import append_toolguard_guidance
from .tools_runtime import canonical_tool_args
from .tools_runtime import check_file_safety
from .tools_runtime import classify_tool_failure
from .tools_runtime import coerce_tool_args
from .tools_runtime import FILE_MUTATING_TOOL_NAMES
from .tools_runtime import file_mutation_result_landed
from .tools_runtime import FORGET_SCHEMA
from .tools_runtime import get_read_block_error
from .tools_runtime import IDEMPOTENT_TOOL_NAMES
from .tools_runtime import is_write_denied
from .tools_runtime import make_tool_result_message
from .tools_runtime import MUTATING_TOOL_NAMES
from .tools_runtime import NativeMemory
from .tools_runtime import RECALL_SCHEMA
from .tools_runtime import REGISTRY
from .tools_runtime import RETAIN_SCHEMA
from .tools_runtime import schema_name
from .tools_runtime import ToolCallGuardrailConfig
from .tools_runtime import ToolCallGuardrailController
from .tools_runtime import ToolCallSignature
from .tools_runtime import toolguard_synthetic_result
from .tools_runtime import ToolGuardrailDecision
from .tools_runtime import WEB_EXTRACT_AVAILABILITY
from .ws import await_future
from .ws import ConnectionManager
from .ws import discard_call
from .ws import discard_user
from .ws import dispatch_user_event
from .ws import Handler
from .ws import JsonRpcDispatcher
from .ws import JsonRpcEmitter
from .ws import JsonRpcError
from .ws import MANAGER
from .ws import new_runtime_session
from .ws import resolve_future
from .ws import runtime_info_snapshot
from .ws import RuntimeSession
from .ws import serialize_settings
from .ws import start_ws_event_loop
from .ws import stop_ws_event_loop

# ─── Tier 6 — core.llm ─────────────────────────────────────────────────────────
# ─── Tier 7 — core.ws ──────────────────────────────────────────────────────────
# ─── Tier 8 — core.tools_runtime ───────────────────────────────────────────────
# ClassifiedError / FailoverReason live in core.llm.error_classifier — re-export via Tier 6.
# ─── Tier 9 — core.async_jobs ─────────────────────────────────────────────────
# ─── Tier 10 — core.chat ──────────────────────────────────────────────────────
# Importing core.chat runs its __init__ which self-registers search_tools
# (from tools_runtime), memory tools (from memory), agent_delegate_tool,
# and the backend_tools registered on import.
# ─── Tier 11 — core.backend_tools ─────────────────────────────────────────────


__all__ = [
    # Tier 5
    "REQUEST_ID_HEADER",
    "adopt_inbound",
    "begin_local_scope",
    "correlated_exception_response",
    "correlation_id_middleware",
    "new_request_id",
    "normalize_inbound",
    "limiter",
    "rate_limit_exception_handler",
    "stash_user_id_middleware",
    "redact_sensitive_text",
    "mask_secret",
    "RedactingFormatter",
    "attachment_root",
    "attachments_gc_session",
    "path_attach_ref",
    "attachments_remove",
    "session_dir",
    "cleanup_expired",
    "temp_files_gc_session",
    "get_file_path",
    "save_file",
    "authenticate_ws_token",
    "AvatarGenerationError",
    "build_system_prompt_extras",
    "generate_avatar",
    "get_active_avatar",
    "get_or_create_persona",
    "list_avatar_history",
    "PersonaValidationError",
    "update_persona",
    "CommandContext",
    "commands_catalog",
    "exec_slash_command",
    # Tier 6
    "client_for_config",
    "client_for_service",
    "client_for_user",
    "get_async_client",
    "MissingLlmConfigError",
    "call_with_retry",
    "LLMRuntimeError",
    "classify_api_error",
    "ClassifiedError",
    "FailoverReason",
    "compress_history_if_needed",
    "resolve_service_row",
    "resolve_user_llm_config",
    # Tier 7
    "MANAGER",
    "ConnectionManager",
    "start_ws_event_loop",
    "stop_ws_event_loop",
    "JsonRpcDispatcher",
    "JsonRpcError",
    "JsonRpcEmitter",
    "Handler",
    "RuntimeSession",
    "new_runtime_session",
    "serialize_settings",
    "runtime_info_snapshot",
    "await_future",
    "discard_call",
    "discard_user",
    "dispatch_user_event",
    "resolve_future",
    # Tier 8
    "REGISTRY",
    "schema_name",
    "ALWAYS_AVAILABLE",
    "WEB_EXTRACT_AVAILABILITY",
    "FORGET_SCHEMA",
    "RECALL_SCHEMA",
    "RETAIN_SCHEMA",
    "NativeMemory",
    "coerce_tool_args",
    "file_mutation_result_landed",
    "FILE_MUTATING_TOOL_NAMES",
    "make_tool_result_message",
    "check_file_safety",
    "ToolCallGuardrailController",
    "ToolCallGuardrailConfig",
    "ToolCallSignature",
    "ToolGuardrailDecision",
    "canonical_tool_args",
    "classify_tool_failure",
    "append_toolguard_guidance",
    "toolguard_synthetic_result",
    "IDEMPOTENT_TOOL_NAMES",
    "MUTATING_TOOL_NAMES",
    "is_write_denied",
    "get_read_block_error",
    # Tier 9
    "create_job",
    "get_job",
    "list_jobs",
    "update_job",
    "pause_job",
    "resume_job",
    "remove_job",
    "start_scheduler",
    "stop_scheduler",
    "run_background_memory_review",
    "auto_generate_title",
    # Tier 10
    "Emitter",
    "HeadlessEmitter",
    "safe_emit",
    "build_system_prompt",
    "build_system_prompt_parts",
    "StreamingThinkScrubber",
    "run_chat_turn",
    "load_user_settings",
    "CORE_TOOLS",
    "IterationBudget",
    "agent_delegate_tool",
    "AGENT_DELEGATE_SCHEMA",
    # Tier 11 (also re-exported schemas/functions)
    "WEB_SEARCH_SCHEMA",
    "web_search_tool",
    "WEB_EXTRACT_SCHEMA",
    "web_extract_tool",
    "CRONJOB_SCHEMA",
    "cronjob",
    "IMAGE_GENERATION_SCHEMA",
    "image_generation_tool",
    "TTS_SCHEMA",
    "text_to_speech_tool",
    "SEND_MESSAGE_SCHEMA",
    "send_message_tool",
    "SEARCH_TOOLS_SCHEMA",
    "search_tools_tool",
    "is_safe_outbound",
    "build_session_messages",
]
