import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js'
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'

import dracoDecoderWasmUrl from '../../../assets/draco/draco_decoder.wasm?url'
import dracoWasmWrapperUrl from '../../../assets/draco/draco_wasm_wrapper.js?url'

let dracoLoaderInstance: DRACOLoader | null = null

export function getDracoLoader(): DRACOLoader | null {
  if (typeof window === 'undefined' || typeof Worker === 'undefined') {
    return null
  }

  if (!dracoLoaderInstance) {
    try {
      dracoLoaderInstance = new DRACOLoader()
      dracoLoaderInstance.setDecoderPath({
        js: dracoWasmWrapperUrl,
        wasm: dracoDecoderWasmUrl
      })
      dracoLoaderInstance.setDecoderConfig({ type: 'wasm' })
      dracoLoaderInstance.preload()
    } catch {
      dracoLoaderInstance = null
    }
  }

  return dracoLoaderInstance
}

/**
 * Returns a new GLTFLoader configured with the singleton DRACOLoader and MeshoptDecoder.
 */
export function createGLTFLoader(): GLTFLoader {
  const loader = new GLTFLoader()
  const draco = getDracoLoader()

  if (draco) {
    loader.setDRACOLoader(draco)
  }

  if (typeof MeshoptDecoder !== 'undefined') {
    try {
      loader.setMeshoptDecoder(MeshoptDecoder)
    } catch {
      /* fallback if wasm initialization fails */
    }
  }

  return loader
}
