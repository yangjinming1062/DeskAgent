import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

// Resolves after `ms` milliseconds. Single shared implementation of the
// `await new Promise(r => setTimeout(r, ms))` idiom — used for ritual-walk
// retry pacing, onboarding-flow backoff, and the per-message perch retry.
export function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}
