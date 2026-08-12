import type { ToolsetId } from '@/shared/lib/toolset-catalog'

interface ModeOptionCopy {
  label: string
  description: string
}

export interface Translations {
  common: {
    apply: string
    back: string
    save: string
    saving: string
    cancel: string
    change: string
    choose: string
    clear: string
    close: string
    collapse: string
    confirm: string
    connect: string
    connecting: string
    continue: string
    copied: string
    copy: string
    copyFailed: string
    delete: string
    docs: string
    done: string
    error: string
    failed: string
    free: string
    loading: string
    notSet: string
    refresh: string
    remove: string
    replace: string
    retry: string
    run: string
    send: string
    set: string
    skip: string
    update: string
    on: string
    off: string
  }

  boot: {
    ready: string
    desktopBootFailedWithMessage: (message: string) => string
    steps: {
      connectingGateway: string
      loadingSettings: string
      loadingSessions: string
      startingDesktopConnection: string
      startingDeskAgentDesktop: string
    }
    errors: {
      backgroundExited: string
      backgroundExitedDuringStartup: string
      backendStopped: string
      desktopBootFailed: string
      gatewaySignInRequired: string
      ipcBridgeUnavailable: string
    }
    failure: {
      title: string
      description: string
      retry: string
      openLogs: string
      retryHint: string
      hideRecentLogs: string
      showRecentLogs: string
    }
  }

  notifications: {
    region: string
    hide: string
    show: string
    more: (count: number) => string
    clearAll: string
    dismiss: string
    details: string
    copyDetail: string
    copyDetailFailed: string
    updateReadyMessage: (count: number) => string
    errors: {
      elevenLabsNeedsKey: string
      elevenLabsRejectedKey: string
      methodNotAllowed: string
      microphonePermission: string
      openaiRejectedApiKey: string
      openaiRejectedApiKeyWithStatus: (status: string) => string
      openaiTtsNeedsKey: string
    }
    voice: {
      configureSpeechToText: string
      couldNotStartSession: string
      microphoneAccessDenied: string
      microphoneConstraintsUnsupported: string
      microphoneFailed: string
      microphoneInUse: string
      microphonePermissionDenied: string
      microphoneStartFailed: string
      microphoneUnsupported: string
      noMicrophone: string
      noSpeechDetected: string
      playbackFailed: string
      recordingFailed: string
      transcriptionFailed: string
      transcriptionUnavailable: string
      tryRecordingAgain: string
      unavailable: string
      invalidTitle: string
      invalidMessage: (name: string) => string
      invalidAction: string
    }
    events: {
      compressionTimeoutTitle: string
      compressionTimeoutMessage: string
      cronTriggeredTitle: string
      cronTriggeredMessage: (name: string | null, jobId: string | number) => string
      backgroundReviewFailedTitle: string
      backgroundReviewFailedMessage: (error: string | null) => string
    }
  }

  settings: {
    closeSettings: string
    exportConfig: string
    importConfig: string
    resetToDefaults: string
    resetConfirm: string
    exportFailed: string
    importFailed: string
    resetFailed: string
    nav: {
      account: string
      mcp: string
      archivedChats: string
      about: string
      appearance: string
      models: string
      toolsets: string
      runner: string
      skills: string
      voices: string
    }
    modeOptions: Record<'light' | 'dark' | 'system', ModeOptionCopy>
    about: {
      heading: string
      version: (value: string) => string
      versionUnavailable: string
      checkForUpdates: string
      checking: string
      upToDate: string
      upToDateWithVersion: (value: string) => string
      updateAvailable: (value: string) => string
      updateDownloaded: (value: string) => string
      updateError: (value: string) => string
      download: string
      restart: string
      later: string
    }
    envActions: {
      actionsFor: (label: string) => string
      credentialActions: string
      docs: string
      hideValue: string
      revealValue: string
      replace: string
      set: string
      clear: string
    }
    mcp: {
      loading: string
      failedLoad: string
      nameRequiredTitle: string
      nameRequiredMessage: string
      objectRequired: string
      invalidJson: string
      saveFailed: string
      removeFailed: string
      gatewayUnavailableTitle: string
      gatewayUnavailableMessage: string
      reloadedTitle: string
      reloadedMessage: string
      reloadFailed: string
      savedTitle: string
      savedMessage: (name: string) => string
      newServer: string
      reload: string
      reloading: string
      emptyTitle: string
      emptyDesc: string
      disabled: string
      editServer: string
      name: string
      serverJson: string
      remove: string
      saveServer: string
    }
    runner: {
      title: string
      intro: string
      loading: string
      failedLoad: string
      save: string
      saveSuccess: string
      saveFailed: string
      invalidYaml: string
      terminal: string
      terminalEnvType: string
      security: string
      securityRedactSecrets: string
      browser: string
      browserEngine: string
      browserRecordSessions: string
      browserAllowPrivateUrls: string
      debug: string
      debugInterrupt: string
      debugVisionTools: string
      auxiliary: string
      auxiliaryVisionTimeout: string
      auxiliaryVisionTemperature: string
    }
    skills: {
      title: string
      intro: string
      loading: string
      loadError: string
      saveError: string
      refreshError: string
      emptyTitle: string
      emptyDesc: string
      hiddenByPlatformTitle: string
      hiddenByPlatformDesc: string
    }
    models: {
      heading: string
      intro: string
      loading: string
      saveFailed: string
      saved: string
      reconnectNotice: string
      baseUrl: string
      baseUrlPlaceholder: string
      apiKey: string
      apiKeyPlaceholder: string
      modelName: string
      modelNamePlaceholder: string
      set: string
      notSet: string
      fingerprint: (fp: string) => string
      reveal: string
      hide: string
      clearKey: string
      clearKeyConfirm: string
      clearAll: string
      clearAllConfirm: string
      capabilities: {
        llm: { title: string; desc: string }
        stt: { title: string; desc: string }
        tts: { title: string; desc: string }
        imageGen: { title: string; desc: string }
        videoGen: { title: string; desc: string }
      }
      providers: {
        heading: string
        intro: string
        addSlot: string
        name: string
        remove: string
        empty: string
      }
    }
    account: {
      heading: string
      loading: string
      saveFailed: string
      saved: string
      webSearch: {
        heading: string
        intro: string
        backend: string
        backendDesc: string
        extractBackend: string
        extractBackendDesc: string
        braveApiKey: string
        braveApiKeyPlaceholder: string
        braveApiKeyDesc: string
        tavilyApiKey: string
        tavilyApiKeyPlaceholder: string
        tavilyApiKeyDesc: string
        tavilyBaseUrl: string
        tavilyBaseUrlPlaceholder: string
        set: string
        notSet: string
        fingerprint: (fp: string) => string
        reveal: string
        hide: string
        clearKey: string
        clearKeyConfirm: string
        backendOptions: {
          ddgs: string
          'brave-free': string
          tavily: string
        }
        extractBackendOptions: {
          tavily: string
          'brave-free': string
          ddgs: string
        }
        unavailable: {
          extractTavilyNoKey: string
          extractNonTavilyNoKey: string
          extractNonTavilyWithKey: string
          searchKeyFallback: (selectedBackend: string) => string
        }
      }
      agentDefaults: {
        heading: string
        intro: string
        reasoningEffort: string
        reasoningEffortDesc: string
        serviceTier: string
        serviceTierDesc: string
        backgroundReview: string
        backgroundReviewDesc: string
        reasoningOptions: {
          minimal: string
          low: string
          medium: string
          high: string
          max: string
        }
        serviceTierOptions: {
          auto: string
          default: string
          flex: string
        }
      }
      contextCompression: {
        heading: string
        intro: string
        enableCompression: string
        enableCompressionDesc: string
        threshold: string
        thresholdDesc: string
        thresholdOptions: {
          '0.5': string
          '0.6': string
          '0.7': string
          '0.8': string
          '0.9': string
        }
      }
      signOut: string
      signOutConfirm: string
    }
  }

  speech: {
    title: string
    intro: string
    loading: string
    sttEnabledTitle: string
    sttEnabledDesc: string
    sttTitle: string
    sttDesc: string
    sttEngineTitle: string
    sttEngineDesc: string
    sttSilentFallbackTitle: string
    sttSilentFallbackDesc: string
    ttsEngineTitle: string
    ttsEngineDesc: string
    engineAuto: string
    engineLocal: string
    engineCloud: string
    engineLocalAvail: string
    engineLocalUnavail: string
    recordingTitle: string
    recordingDesc: string
    save: string
    saving: string
    saved: string
    saveFailed: string
  }

  voiceGallery: {
    title: string
    intro: string
    loading: string
    empty: string
    error: string
    provider: string
    preview: string
    playing: string
    all: string
    designSupported: string
  }

  skills: {
    tabSkills: string
    tabToolsets: string
    all: string
    other: string
    searchSkills: string
    searchToolsets: string
    refresh: string
    refreshing: string
    loading: string
    noSkillsTitle: string
    noSkillsDesc: string
    loadFailedTitle: string
    loadFailedDesc: string
    noToolsetsTitle: string
    noToolsetsDesc: string
    noDescription: string
    configured: string
    needsKeys: string
    toolsetsEnabled: (enabled: number, total: number) => string
    configureToolset: (label: string) => string
    toggleToolset: (label: string) => string
    skillsLoadFailed: string
    toolsetsRefreshFailed: string
    skillEnabled: string
    skillDisabled: string
    toolsetEnabled: string
    toolsetDisabled: string
    appliesToNewSessions: (name: string) => string
    failedToUpdate: (name: string) => string
  }

  // Per-toolset label/description. Keyed by toolset id from
  // `lib/toolset-catalog.ts`; the union is derived from TOOLSET_CATALOG so
  // adding a toolset without adding a matching localized pair in en.ts / zh.ts
  // is a compile error.
  toolsets: {
    [K in ToolsetId]: {
      label: string
      description: string
    }
  }

  errors: {
    genericFailure: string
    boundaryTitle: string
    boundaryDesc: string
    reloadWindow: string
    openLogs: string
  }

  ui: {
    search: {
      clear: string
    }
    pagination: {
      label: string
      previous: string
      previousAria: string
      next: string
      nextAria: string
    }
    sidebar: {
      title: string
      description: string
      toggle: string
    }
  }
}
