import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'

let dracoLoaderInstance: DRACOLoader | null = null

export function getDracoLoader(): DRACOLoader | null {
  if (typeof window === 'undefined' || typeof Worker === 'undefined') {
    return null
  }

  if (!dracoLoaderInstance) {
    try {
      dracoLoaderInstance = new DRACOLoader()
      dracoLoaderInstance.setDecoderPath('./draco/')
      dracoLoaderInstance.preload()
    } catch {
      dracoLoaderInstance = null
    }
  }

  return dracoLoaderInstance
}

/**
 * Returns a new GLTFLoader configured with the singleton DRACOLoader.
 */
export function createGLTFLoader(): GLTFLoader {
  const loader = new GLTFLoader()
  const draco = getDracoLoader()

  if (draco) {
    loader.setDRACOLoader(draco)
  }

  return loader
}
