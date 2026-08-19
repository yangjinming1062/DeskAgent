// 三态的密钥字段写入器，供账户表单保存路径使用。
// 后端区分以下三种情况：
//
//   1. PATCH body 中缺该键 → 保留已有值不动
//   2. 键存在，value = `clearedSentinel` → 删除已存储的值
//   3. 键存在，value = 任意其他字符串 → 存该值
//
// `value === ''` 被视作「未修改」，避免未触碰的输入框把已存储的凭据
// 覆盖为空串。需要表达「显式清空」时，调用方应改设 `cleared = true`。
export type SecretFieldBody<T> = { omit: true } | { omit: false; value: T }

export function buildSecretFieldBody<T>(value: string, cleared: boolean, clearedSentinel: T): SecretFieldBody<T> {
  if (cleared) {
    return { omit: false, value: clearedSentinel }
  }

  if (value === '') {
    return { omit: true }
  }

  return { omit: false, value: value as unknown as T }
}
