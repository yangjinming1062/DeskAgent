// 日记页：左月历（带点）+ 右当天正文 + 心情 + 关联片刻 + 补写入口。

import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'
import type React from 'react'

import styles from './diary.module.css'
import { $diaryByDate, $diaryLoading, appendDiary, hydrateDiary } from './journal-store'

function localDateKey(d: Date): string {
  const year = d.getFullYear()
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')

  return `${year}-${month}-${day}`
}

function todayKey(): string {
  return localDateKey(new Date())
}

function monthKey(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`
}

function daysInMonth(date: Date): Date[] {
  const year = date.getFullYear()
  const month = date.getMonth()
  const count = new Date(year, month + 1, 0).getDate()

  return Array.from({ length: count }, (_, i) => new Date(year, month, i + 1))
}

function cursorMonthStart(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1)
}

export function DiaryPage(): React.JSX.Element {
  const diaryByDate = useStore($diaryByDate)
  const loading = useStore($diaryLoading)
  const [selectedDate, setSelectedDate] = useState<string>(todayKey())
  const [draft, setDraft] = useState<string>('')
  const [cursor, setCursor] = useState<Date>(new Date())

  useEffect(() => {
    void hydrateDiary()
  }, [])

  // 月份切换时若 selectedDate 不在新月可见范围里，把它吸到新月第一天。
  useEffect(() => {
    const cursorStart = cursorMonthStart(cursor)
    const cursorEnd = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 0)
    const startKey = localDateKey(cursorStart)
    const endKey = localDateKey(cursorEnd)

    if (selectedDate < startKey || selectedDate > endKey) {
      setSelectedDate(startKey)
    }
  }, [cursor, selectedDate])

  const days = useMemo(() => daysInMonth(cursor), [cursor])
  const firstDayOffset = days[0] ? (days[0].getDay() + 6) % 7 : 0
  const entry = diaryByDate[selectedDate]

  const saveDraft = async (): Promise<void> => {
    const text = draft.trim()

    if (!text) {
      return
    }

    // 保留现有 mood，避免补写时把心情字段吞掉。
    await appendDiary({ body: text, date: selectedDate, mood: entry?.mood ?? undefined })
    setDraft('')
  }

  return (
    <div className={styles.shell}>
      <aside className={styles.calendar}>
        <div className={styles.calendarHeader}>
          <button
            className={styles.monthButton}
            onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))}
            type="button"
          >
            ←
          </button>
          <span className={styles.monthLabel}>{monthKey(cursor)}</span>
          <button
            className={styles.monthButton}
            onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))}
            type="button"
          >
            →
          </button>
        </div>

        <div className={styles.weekHeader}>
          {['一', '二', '三', '四', '五', '六', '日'].map(d => (
            <span className={styles.weekDay} key={d}>
              {d}
            </span>
          ))}
        </div>

        <div className={styles.grid}>
          {days.map((d, index) => {
            const key = localDateKey(d)
            const hasEntry = Boolean(diaryByDate[key])
            const selected = key === selectedDate

            return (
              <button
                className={`${styles.dayCell} ${selected ? styles.dayCellSelected : ''} ${hasEntry ? styles.dayCellHasEntry : ''}`}
                key={key}
                onClick={() => setSelectedDate(key)}
                style={index === 0 && firstDayOffset > 0 ? { gridColumnStart: firstDayOffset + 1 } : undefined}
                type="button"
              >
                {d.getDate()}
                {hasEntry && <span className={styles.dot} />}
              </button>
            )
          })}
        </div>
      </aside>

      <main className={styles.entry}>
        <header className={styles.entryHeader}>
          <h2 className={styles.entryDate}>{selectedDate}</h2>
          {entry?.mood && <span className={styles.mood}>心情 · {entry.mood}</span>}
        </header>

        {loading ? (
          <p className={styles.loading}>翻开中…</p>
        ) : entry ? (
          <p className={styles.body}>{entry.body}</p>
        ) : (
          <p className={styles.empty}>今天还没有日记。要不要写点什么？</p>
        )}

        <textarea
          className={styles.editor}
          onChange={e => setDraft(e.target.value)}
          placeholder="补写今天（不会被现有正文覆盖）"
          value={draft}
        />
        <button className={styles.saveButton} disabled={!draft.trim()} onClick={() => void saveDraft()} type="button">
          保存
        </button>
      </main>
    </div>
  )
}
