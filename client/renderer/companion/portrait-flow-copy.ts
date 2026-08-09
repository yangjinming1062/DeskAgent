// Single source of truth for the two-step avatar→fullbody flow's user-visible
// copy. Onboarding / settings / PersonaSection / PersonaRetune all import from
// here so a wording change is one edit, not four.

export const TWO_STEP_INTRO_HINT = '形象生成有两步：先确认相貌，再生成全身。每一张图都可以单独反馈意见。'

export const TWO_STEP_AVATAR_PROMPT = '已保存 — 先按新性格重新生成头像？'
export const TWO_STEP_FULLBODY_PROMPT = '头像已更新 — 再生成全身图？'
