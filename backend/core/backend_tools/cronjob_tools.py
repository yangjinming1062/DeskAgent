import json

from components import coerce_int
from components import get_logger
from components import tool_error

from ..async_jobs.cron import create_job
from ..async_jobs.cron import get_job
from ..async_jobs.cron import list_jobs
from ..async_jobs.cron import pause_job
from ..async_jobs.cron import remove_job
from ..async_jobs.cron import resume_job
from ..async_jobs.cron import update_job
from ..tools_runtime.registry import ALWAYS_AVAILABLE
from ..tools_runtime.registry import REGISTRY

logger = get_logger(__name__)


def _build_updates(prompt: str | None, name: str | None, schedule: str | None) -> dict | None:
    updates: dict = {}
    if prompt is not None:
        updates["prompt"] = prompt
    if name is not None:
        updates["name"] = name
    if schedule is not None:
        updates["schedule"] = schedule
        # Providing a new schedule unpauses the job.
        updates["is_paused"] = False
    return updates or None


def _handle_cron_action(
    action: str,
    user_id: int,
    job_id_raw,
    prompt: str | None,
    schedule: str | None,
    name: str | None,
    deliver: str,
) -> str:
    match action:
        case "create":
            if not schedule or not prompt:
                return tool_error("schedule and prompt are required for create")
            job = create_job(user_id=user_id, prompt=prompt, schedule=schedule, name=name or "cron job", deliver=deliver)
            return json.dumps({"success": True, "message": f"Cron job '{job.get('name')}' created.", "job": job}, ensure_ascii=False)
        case "list":
            jobs = list_jobs(user_id=user_id)
            return json.dumps({"success": True, "jobs": jobs}, ensure_ascii=False)
        case "update":
            if job_id_raw is None:
                return tool_error("job_id is required for update")
            updates = _build_updates(prompt, name, schedule)
            job = update_job(user_id=user_id, job_id=coerce_int(job_id_raw, None), updates=updates)
            return json.dumps({"success": True, "message": f"Cron job #{job['id']} updated.", "job": job}, ensure_ascii=False)
        case "remove":
            if job_id_raw is None:
                return tool_error("job_id is required for remove")
            remove_job(user_id=user_id, job_id=coerce_int(job_id_raw, None))
            return json.dumps({"success": True, "message": f"Cron job #{job_id_raw} removed."}, ensure_ascii=False)
        case "pause":
            if job_id_raw is None:
                return tool_error("job_id is required for pause")
            job = pause_job(user_id=user_id, job_id=coerce_int(job_id_raw, None))
            return json.dumps({"success": True, "message": f"Cron job #{job['id']} paused.", "job": job}, ensure_ascii=False)
        case "resume":
            if job_id_raw is None:
                return tool_error("job_id is required for resume")
            job = resume_job(user_id=user_id, job_id=coerce_int(job_id_raw, None))
            return json.dumps({"success": True, "message": f"Cron job #{job['id']} resumed.", "job": job}, ensure_ascii=False)
        case "get":
            if job_id_raw is None:
                return tool_error("job_id is required for get")
            job = get_job(user_id=user_id, job_id=coerce_int(job_id_raw, None))
            if not job:
                return tool_error(f"Job {job_id_raw} not found")
            return json.dumps({"success": True, "job": job}, ensure_ascii=False)
        case _:
            return tool_error(f"Unknown cronjob action: {action!r}. Allowed: create, list, update, remove, pause, resume, get.")


def cronjob(
    action: str,
    user_id: int,
    job_id: int | None = None,
    prompt: str | None = None,
    schedule: str | None = None,
    name: str | None = None,
    deliver: str = "local",
    **kwargs,
) -> str:
    normalized = (action or "").strip().lower()
    try:
        return _handle_cron_action(normalized, user_id, job_id, prompt, schedule, name, deliver)
    except Exception as e:
        logger.exception("cronjob tool error")
        return tool_error(str(e))


CRONJOB_SCHEMA = {
    "name": "cronjob",
    "description": "Manage scheduled cron jobs. Jobs are checked against their schedules and executed.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "One of: create, list, update, pause, resume, remove."},
            "job_id": {"type": "integer", "description": "Required for update/pause/resume/remove."},
            "prompt": {"type": "string", "description": "For create: the full prompt/instructions for the job."},
            "schedule": {"type": "string", "description": "For create/update: cron expression (e.g., '0 9 * * *' for daily at 9am)."},
            "name": {"type": "string", "description": "Optional human-friendly name."},
            "deliver": {
                "type": "string",
                "description": "Delivery channel for job output (e.g., 'local', 'webhook'). Defaults to 'local' when omitted.",
                "default": "local",
            },
        },
        "required": ["action"],
    },
}

REGISTRY.register("cronjob", CRONJOB_SCHEMA, cronjob, ALWAYS_AVAILABLE)
