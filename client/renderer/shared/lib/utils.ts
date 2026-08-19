import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

// 在 `ms` 毫秒后 resolve。是 `await new Promise(r => setTimeout(r, ms))`
// 习惯用法的统一实现——用于仪式行走的重试节奏、onboarding 流程的退避
// 以及每条消息的栖位重试。
export function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}
