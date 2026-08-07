import { useEffect, useState } from 'react'
import { useStore } from '@nanostores/react'
import { getApiClient } from '../state/auth-store'
import { $wardrobeItems, $wardrobeOpen, applyOutfit, type WardrobeItem } from '../state/wardrobe-store'

export function WardrobePanel(): React.JSX.Element {
  const items = useStore($wardrobeItems)
  const open = useStore($wardrobeOpen)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open || items.length > 0) return
    const api = getApiClient()
    setLoading(true)
    api.getWardrobe().then(data => {
      $wardrobeItems.set(data)
      const equipped = data.find(i => i.equipped)
      if (equipped) applyOutfit(equipped)
    }).catch(console.error).finally(() => setLoading(false))
  }, [open])

  const equip = async (item: WardrobeItem): Promise<void> => {
    const api = getApiClient()
    try {
      const updated = await api.equipOutfit(item.id)
      $wardrobeItems.set($wardrobeItems.get().map(i => ({ ...i, equipped: i.id === item.id })))
      applyOutfit(updated)
    } catch (err) {
      console.error('Equip failed:', err)
    }
  }

  if (!open) return <></>

  return (
    <div className="wardrobe-panel">
      <div className="wardrobe-header">
        <span>衣橱</span>
        <button className="wardrobe-close" onClick={() => $wardrobeOpen.set(false)}>✕</button>
      </div>
      <div className="wardrobe-list">
        {loading && <div className="wardrobe-empty">加载中…</div>}
        {!loading && items.length === 0 && <div className="wardrobe-empty">暂无服装</div>}
        {items.map(item => (
          <button
            key={item.id}
            className={`wardrobe-item ${item.equipped ? 'active' : ''}`}
            onClick={() => void equip(item)}
          >
            <span
              className="wardrobe-swatch"
              style={{
                background: item.material_overrides['*']?.color ?? 'transparent',
                border: item.category === 'preset' && !item.material_overrides['*'] ? '2px dashed rgba(255,255,255,0.3)' : 'none'
              }}
            />
            <span className="wardrobe-name">{item.name}</span>
            {item.category === 'generated' && <span className="wardrobe-tag">AI</span>}
            {item.equipped && <span className="wardrobe-check">✓</span>}
          </button>
        ))}
      </div>
    </div>
  )
}
