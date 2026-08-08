import { useState } from 'react'

import { Button, Input } from '@/shared/components/ui'
import { Loader2 } from '@/shared/lib/icons'
import { notify, notifyError } from '@/shared/store/notifications'
import { strings } from '@/shared/strings'

import { ListRow } from '../primitives'

export function ChangePasswordForm(): React.JSX.Element {
  const t = strings
  const a = t.settings.account.changePassword

  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)

  const handleSubmit = async () => {
    if (busy) {
      return
    }

    if (newPassword.length < 8) {
      notify({ kind: 'error', title: a.title, message: a.tooShort })

      return
    }

    if (newPassword !== confirm) {
      notify({ kind: 'error', title: a.title, message: a.mismatch })

      return
    }

    if (currentPassword === newPassword) {
      notify({ kind: 'error', title: a.title, message: a.sameAsOld })

      return
    }

    setBusy(true)

    try {
      const result = await window.deskagent.changePassword({
        current_password: currentPassword,
        new_password: newPassword
      })

      notify({ kind: 'success', title: a.title, message: result.message || a.success })
      setCurrentPassword('')
      setNewPassword('')
      setConfirm('')
    } catch (err) {
      notifyError(err, a.title)
    } finally {
      setBusy(false)
    }
  }

  return (
    <ListRow
      description={a.title}
      title={
        <div className="flex items-center gap-3">
          <Input
            className="max-w-xs"
            disabled={busy}
            onChange={event => setCurrentPassword(event.currentTarget.value)}
            placeholder={a.currentPassword}
            type="password"
            value={currentPassword}
          />
          <Input
            className="max-w-xs"
            disabled={busy}
            onChange={event => setNewPassword(event.currentTarget.value)}
            placeholder={a.newPassword}
            type="password"
            value={newPassword}
          />
          <Input
            className="max-w-xs"
            disabled={busy}
            onChange={event => setConfirm(event.currentTarget.value)}
            placeholder={a.confirmPassword}
            type="password"
            value={confirm}
          />
          <Button
            disabled={busy || !currentPassword || !newPassword || !confirm}
            onClick={() => void handleSubmit()}
            size="sm"
          >
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : null}
            {a.submit}
          </Button>
        </div>
      }
      wide
    />
  )
}
