export interface PickedImage {
  base64: string
  contentType: string
  previewUrl: string
}

// Matches the backend's AvatarUploadRequest / AvatarFromImageRequest cap —
// anything bigger comes back as a 422 the user can't act on, so reject it here
// with a hint instead.
const MAX_IMAGE_BASE64 = 8 * 1024 * 1024

/** `null` when the user cancels or the file is unreadable; `error` is user-facing copy. */
export async function pickAvatarImage(title: string): Promise<{ image: PickedImage } | { error: string } | null> {
  try {
    const [path] = await window.deskagent.selectPaths({
      title,
      filters: [{ name: 'Images', extensions: ['png', 'jpg', 'jpeg', 'webp', 'gif'] }]
    })

    if (!path) {
      return null
    }

    const dataUrl = await window.deskagent.readFileDataUrl(path)
    const comma = dataUrl.indexOf(',')
    const base64 = comma > 0 ? dataUrl.slice(comma + 1) : ''

    if (!base64) {
      return null
    }

    // The backend splits the mime on ';', so a trailing ";base64" is harmless.
    return base64.length > MAX_IMAGE_BASE64
      ? { error: '这张图太大了，换张小一点的吧' }
      : { image: { base64, contentType: dataUrl.slice(5, comma), previewUrl: dataUrl } }
  } catch {
    return { error: '选择图片失败了，换个方式试试？' }
  }
}

/** `null` on failure — the raw URL is the thing the renderer can't reach, so returning it would render a broken image. */
export async function resolvePortraitUrl(assetUrl: string | null | undefined): Promise<string | null> {
  if (!assetUrl) {
    return null
  }

  try {
    return await window.deskagent.apiAsset({ url: assetUrl })
  } catch {
    return null
  }
}
