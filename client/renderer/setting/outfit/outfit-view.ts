import { atom } from 'nanostores'

/** 「换一身 / 形象」面板的 tab：
 *  - `wardrobe`          —— 衣柜（仅 2D 渲染模式可见）
 *  - `sprite-appearance` —— 渲染模式 / 切分重试 / 缩放
 */
export type OutfitView = 'wardrobe' | 'sprite-appearance'

export const $outfitView = atom<OutfitView>('wardrobe')

export function setOutfitView(view: OutfitView): void {
  $outfitView.set(view)
}
