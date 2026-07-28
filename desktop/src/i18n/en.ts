import type { Translations } from './types'

export const en: Translations = {
  common: {
    apply: 'Apply',
    back: 'Back',
    save: 'Save',
    saving: 'Saving…',
    cancel: 'Cancel',
    change: 'Change',
    choose: 'Choose',
    clear: 'Clear',
    close: 'Close',
    collapse: 'Collapse',
    confirm: 'Confirm',
    connect: 'Connect',
    connecting: 'Connecting',
    continue: 'Continue',
    copied: 'Copied',
    copy: 'Copy',
    copyFailed: 'Copy failed',
    delete: 'Delete',
    docs: 'Docs',
    done: 'Done',
    error: 'Error',
    failed: 'Failed',
    free: 'Free',
    loading: 'Loading…',
    notSet: 'Not set',
    refresh: 'Refresh',
    remove: 'Remove',
    replace: 'Replace',
    retry: 'Retry',
    run: 'Run',
    send: 'Send',
    set: 'Set',
    skip: 'Skip',
    update: 'Update',
    on: 'On',
    off: 'Off'
  },

  boot: {
    ready: 'Zast Desktop is ready',
    desktopBootFailedWithMessage: message => `Desktop boot failed: ${message}`,
    steps: {
      connectingGateway: 'Connecting live desktop gateway',
      loadingSettings: 'Loading Zast settings',
      loadingSessions: 'Loading recent sessions',
      startingDesktopConnection: 'Starting desktop connection',
      startingZastDesktop: 'Starting Zast Desktop…'
    },
    errors: {
      backgroundExited: 'Zast background process exited.',
      backgroundExitedDuringStartup: 'Zast background process exited during startup.',
      backendStopped: 'Backend stopped',
      desktopBootFailed: 'Desktop boot failed',
      gatewaySignInRequired: 'Gateway sign-in required',
      ipcBridgeUnavailable: 'Desktop IPC bridge is unavailable.'
    },
    failure: {
      title: "Zast couldn't start",
      description:
        "The background gateway didn't come up. Try one of the recovery steps below. Nothing here deletes your chats or settings.",
      retry: 'Retry',
      openLogs: 'Open logs',
      retryHint: 'Reload re-dials the cloud Backend. Open logs to see why the previous attempt failed.',
      hideRecentLogs: 'Hide recent logs',
      showRecentLogs: 'Show recent logs'
    }
  },

  notifications: {
    region: 'Notifications',
    hide: 'Hide',
    show: 'Show',
    more: count => `${count} more ${count === 1 ? 'notification' : 'notifications'}`,
    clearAll: 'Clear all',
    dismiss: 'Dismiss notification',
    details: 'Details',
    copyDetail: 'Copy detail',
    copyDetailFailed: 'Could not copy notification detail',
    updateReadyMessage: count => `${count} new change${count === 1 ? '' : 's'} available.`,
    errors: {
      elevenLabsNeedsKey: 'ElevenLabs STT needs ELEVENLABS_API_KEY.',
      elevenLabsRejectedKey: 'ElevenLabs rejected the API key (401).',
      methodNotAllowed:
        'The desktop backend rejected that request (405 Method Not Allowed). Try restarting Zast Desktop.',
      microphonePermission: 'Microphone permission was denied.',
      openaiRejectedApiKey: 'OpenAI rejected the API key.',
      openaiRejectedApiKeyWithStatus: status => `OpenAI rejected the API key (${status} invalid_api_key).`,
      openaiTtsNeedsKey: 'OpenAI TTS needs VOICE_TOOLS_OPENAI_KEY or OPENAI_API_KEY.'
    },
    voice: {
      configureSpeechToText: 'Configure speech-to-text to use voice mode.',
      couldNotStartSession: 'Could not start voice session',
      microphoneAccessDenied: 'Microphone access denied.',
      microphoneConstraintsUnsupported: 'Microphone constraints are not supported by this device.',
      microphoneFailed: 'Microphone failed',
      microphoneInUse: 'Microphone is already in use by another app.',
      microphonePermissionDenied: 'Microphone permission was denied.',
      microphoneStartFailed: 'Could not start microphone recording.',
      microphoneUnsupported: 'This runtime does not support microphone recording.',
      noMicrophone: 'No microphone was found.',
      noSpeechDetected: 'No speech detected',
      playbackFailed: 'Voice playback failed',
      recordingFailed: 'Voice recording failed',
      transcriptionFailed: 'Voice transcription failed',
      transcriptionUnavailable: 'Voice transcription is not available yet.',
      tryRecordingAgain: 'Try recording again.',
      unavailable: 'Voice unavailable'
    },
    events: {
      referencesTitle: 'References',
      referencesMessage: items => items,
      compressionTimeoutTitle: 'Context compression',
      compressionTimeoutMessage: 'Compression request timed out — continuing without compression.',
      cronTriggeredTitle: 'Cron job triggered',
      cronTriggeredMessage: (name, jobId) => name || `Job #${jobId}`,
      backgroundReviewFailedTitle: 'Background review',
      backgroundReviewFailedMessage: error => error || 'Memory extraction failed'
    }
  },

  titlebar: {
    hideSidebar: 'Hide sidebar',
    showSidebar: 'Show sidebar',
    search: 'Search',
    searchTitle: 'Search sessions, views, and actions',
    swapSidebarSides: 'Swap sidebar sides',
    swapSidebarSidesTitle: 'Swap the sessions and file browser sides',
    hideRightSidebar: 'Hide right sidebar',
    showRightSidebar: 'Show right sidebar',
    muteHaptics: 'Mute haptics',
    unmuteHaptics: 'Unmute haptics',
    openSettings: 'Open settings',
    openKeybinds: 'Keyboard shortcuts'
  },

  keybinds: {
    title: 'Keyboard shortcuts',
    subtitle: open => `Click a shortcut to rebind it · ${open} reopens this panel.`,
    rebind: 'Rebind',
    reset: 'Reset to default',
    resetAll: 'Reset all',
    pressKey: 'Press a key…',
    set: 'set',
    conflictWith: label => `Also bound to “${label}”`,
    categories: {
      composer: 'Composer',
      profiles: 'Profiles',
      session: 'Session',
      navigation: 'Navigation',
      view: 'View'
    },
    actions: {
      'keybinds.openPanel': 'Open keyboard shortcuts',
      'nav.commandPalette': 'Open command palette',
      'nav.commandCenter': 'Open command center',
      'nav.settings': 'Open settings',
      'nav.profiles': 'Open profiles',
      'nav.artifacts': 'Open artifacts',
      'nav.agents': 'Open agents',
      'session.new': 'New session',
      'session.next': 'Next session',
      'session.prev': 'Previous session',
      'session.focusSearch': 'Search sessions',
      'session.togglePin': 'Pin / unpin current session',
      'composer.focus': 'Focus composer',
      'composer.modelPicker': 'Open model picker',
      'view.toggleSidebar': 'Toggle sessions sidebar',
      'view.toggleRightSidebar': 'Toggle file browser',
      'view.showFiles': 'Show file browser',
      'view.showTerminal': 'Show terminal',
      'view.terminalSelection': 'Send terminal selection to composer',
      'view.closePreviewTab': 'Close preview tab',
      'view.flipPanes': 'Swap sidebar sides',
      'appearance.toggleMode': 'Toggle light / dark',
      'profile.default': 'Switch to default profile',
      'profile.switch.1': 'Switch to profile 1',
      'profile.switch.2': 'Switch to profile 2',
      'profile.switch.3': 'Switch to profile 3',
      'profile.switch.4': 'Switch to profile 4',
      'profile.switch.5': 'Switch to profile 5',
      'profile.switch.6': 'Switch to profile 6',
      'profile.switch.7': 'Switch to profile 7',
      'profile.switch.8': 'Switch to profile 8',
      'profile.switch.9': 'Switch to profile 9',
      'profile.switch.10': 'Switch to profile 10',
      'profile.switch.11': 'Switch to profile 11',
      'profile.switch.12': 'Switch to profile 12',
      'profile.switch.13': 'Switch to profile 13',
      'profile.switch.14': 'Switch to profile 14',
      'profile.switch.15': 'Switch to profile 15',
      'profile.switch.16': 'Switch to profile 16',
      'profile.switch.17': 'Switch to profile 17',
      'profile.switch.18': 'Switch to profile 18',
      'profile.next': 'Next profile',
      'profile.prev': 'Previous profile',
      'profile.toggleAll': 'Toggle all-profiles view',
      'profile.create': 'Create profile',
      'composer.send': 'Send message',
      'composer.newline': 'Insert newline',
      'composer.steer': 'Steer the running turn',
      'composer.sendQueued': 'Send next queued turn',
      'composer.mention': 'Reference files, folders, URLs',
      'composer.slash': 'Slash command palette',
      'composer.help': 'Quick help',
      'composer.history': 'Cycle popover / history',
      'composer.cancel': 'Close popover · cancel run'
    }
  },

  login: {
    backendUnreachable: 'Cannot reach the backend. Check your network and try again.',
    error: 'Invalid username or password.',
    password: 'Password',
    signIn: 'Sign in',
    signingIn: 'Signing in…',
    signOut: 'Sign out',
    subtitle: 'Sign in with your Zast account to continue.',
    title: 'Sign in to Zast',
    username: 'Username'
  },

  language: {
    label: 'Language',
    description: 'Choose the language for the desktop interface.',
    saving: 'Saving language…',
    saveError: 'Language update failed',
    switchTo: 'Switch language',
    searchPlaceholder: 'Search languages…',
    noResults: 'No languages found'
  },

  settings: {
    closeSettings: 'Close settings',
    exportConfig: 'Export config',
    importConfig: 'Import config',
    resetToDefaults: 'Reset to defaults',
    resetConfirm: 'Reset all settings to Zast defaults?',
    exportFailed: 'Export failed',
    resetFailed: 'Reset failed',
    nav: {
      account: 'Account',
      mcp: 'MCP',
      archivedChats: 'Archived Chats',
      about: 'About',
      appearance: 'Appearance',
      toolsets: 'Toolsets',
      runner: 'Runner',
      skills: 'Skills & Tools'
    },
    modeOptions: {
      light: { label: 'Light', description: 'Bright desktop surfaces' },
      dark: { label: 'Dark', description: 'Low-glare workspace' },
      system: { label: 'System', description: 'Follow OS appearance' }
    },
    appearance: {
      title: 'Appearance',
      intro:
        'These are desktop-only display preferences. Mode controls brightness; theme controls the accent palette and chat surface styling.',
      colorMode: 'Color Mode',
      colorModeDesc: 'Pick a fixed mode or let Zast follow your system setting.',
      toolViewTitle: 'Tool Call Display',
      toolViewDesc: 'Product hides raw tool payloads; Technical shows full input/output.',
      product: 'Product',
      productDesc: 'Human-friendly tool activity with concise summaries.',
      technical: 'Technical',
      technicalDesc: 'Include raw tool args/results and low-level details.',
      themeTitle: 'Theme',
      themeDesc: 'Desktop palettes only. The selected mode is applied on top.',
      themeProfileNote: profile => `Saved for the ${profile} profile — each profile keeps its own theme.`
    },
    about: {
      heading: 'Zast Desktop',
      version: value => `Version ${value}`,
      versionUnavailable: 'Version unavailable',
      checkForUpdates: 'Check for updates',
      checking: 'Checking…',
      upToDate: 'You are up to date',
      upToDateWithVersion: value => `You are on the latest version (v${value})`,
      updateAvailable: value => `Version v${value} is available`,
      updateDownloaded: value => `v${value} is ready to install`,
      updateError: value => `Update check failed: ${value}`,
      download: 'Download update',
      restart: 'Restart now',
      later: 'Later'
    },
    envActions: {
      actionsFor: label => `Actions for ${label}`,
      credentialActions: 'Credential actions',
      docs: 'Docs',
      hideValue: 'Hide value',
      revealValue: 'Reveal value',
      replace: 'Replace',
      set: 'Set',
      clear: 'Clear'
    },
    mcp: {
      loading: 'Loading MCP servers...',
      failedLoad: 'MCP config failed to load',
      nameRequiredTitle: 'Name required',
      nameRequiredMessage: 'Give this MCP server a config key.',
      objectRequired: 'Server config must be a JSON object',
      invalidJson: 'Invalid MCP JSON',
      saveFailed: 'Save failed',
      saveRestartFailed: (error: string) => `Configuration saved, but the runner failed to restart: ${error}`,
      removeFailed: 'Remove failed',
      gatewayUnavailableTitle: 'Gateway unavailable',
      gatewayUnavailableMessage: 'Reconnect the gateway before reloading MCP.',
      reloadedTitle: 'MCP tools reloaded',
      reloadedMessage: 'New tool schemas apply to fresh turns.',
      reloadFailed: 'MCP reload failed',
      savedTitle: 'MCP server saved',
      savedMessage: name => `${name} applies after MCP reload.`,
      newServer: 'New server',
      reload: 'Reload MCP',
      reloading: 'Reloading...',
      emptyTitle: 'No MCP servers',
      emptyDesc: 'Add a stdio or HTTP server to expose MCP tools.',
      disabled: 'disabled',
      editServer: 'Edit server',
      name: 'Name',
      serverJson: 'Server JSON',
      remove: 'Remove',
      saveServer: 'Save server'
    },
    sessions: {
      loading: 'Loading archived sessions…',
      archivedTitle: 'Archived sessions',
      archivedIntro:
        'Archived chats are hidden from the sidebar but keep all their messages. Ctrl/⌘-click a chat in the sidebar to archive it.',
      emptyArchivedTitle: 'Nothing archived',
      emptyArchivedDesc: 'Archive a chat to hide it here.',
      unarchive: 'Unarchive',
      deletePermanently: 'Delete permanently',
      messages: count => `${count} ${count === 1 ? 'message' : 'messages'}`,
      restored: 'Restored',
      deleteConfirm: title => `Permanently delete "${title}"? This cannot be undone.`,
      defaultDirTitle: 'Default project directory',
      defaultDirDesc:
        'New sessions start in this folder unless you pick another. Leave it unset to use your home directory.',
      defaultDirUpdated: 'Default project directory updated',
      defaultsTo: label => `Defaults to ${label}.`,
      change: 'Change',
      choose: 'Choose',
      clear: 'Clear',
      notSet: 'Not set',
      failedLoad: 'Could not load archived sessions',
      unarchiveFailed: 'Unarchive failed',
      deleteFailed: 'Delete failed',
      updateDirFailed: 'Could not update default directory',
      clearDirFailed: 'Could not clear default directory'
    },
    runner: {
      title: 'Runner Configuration',
      intro: 'Configure the underlying runner settings. Modifying these requires restarting the runner.',
      loading: 'Loading runner configuration...',
      failedLoad: 'Failed to load runner configuration',
      save: 'Save Config',
      saveSuccess: 'Configuration saved. Restarting runner...',
      saveFailed: 'Failed to save configuration',
      saveRestartFailed: (error: string) => `Configuration saved, but the runner failed to restart: ${error}`,
      invalidYaml: 'Invalid YAML configuration',
      terminal: 'Terminal Settings',
      terminalEnvType: 'Environment Type',
      security: 'Security',
      securityRedactSecrets: 'Redact Secrets',
      browser: 'Browser Settings',
      browserEngine: 'Browser Engine',
      browserRecordSessions: 'Record Sessions',
      browserAllowPrivateUrls: 'Allow Private URLs',
      debug: 'Debug Toggles',
      debugInterrupt: 'Interrupt Mode',
      debugVisionTools: 'Debug Vision Tools',
      auxiliary: 'Auxiliary Tools',
      auxiliaryVisionTimeout: 'Vision Model Timeout (s)',
      auxiliaryVisionTemperature: 'Vision Model Temperature'
    },
    toolsets: {
      loadingConfig: 'Loading configuration',
      savedTitle: 'Credential saved',
      savedMessage: key => `${key} updated.`,
      removedTitle: 'Credential removed',
      removedMessage: key => `${key} removed.`,
      failedSave: key => `Failed to save ${key}`,
      failedRemove: key => `Failed to remove ${key}`,
      failedReveal: key => `Failed to reveal ${key}`,
      removeConfirm: key => `Remove ${key} from .env?`,
      set: 'Set',
      notSet: 'Not set',
      selectedTitle: 'Provider selected',
      selectedMessage: provider => `${provider} is now active.`,
      failedSelect: provider => `Failed to select ${provider}`,
      failedLoad: 'Tool configuration failed to load',
      noProviderOptions: 'This toolset has no provider options — enable it and it works with your current setup.',
      noProviders: 'No providers are available for this toolset right now.',
      ready: 'Ready',
      nousIncluded: 'Included with a Nous subscription — sign in to Nous Portal to activate.',
      noApiKeyRequired: 'No API key required.',
      postSetupHint: step =>
        `This backend needs a one-time install (${step}). Runs on this machine — may take a few minutes.`,
      postSetupRun: 'Run setup',
      postSetupRunning: 'Installing…',
      postSetupStarting: 'Starting…',
      postSetupCompleteTitle: 'Setup complete',
      postSetupCompleteMessage: step => `${step} installed.`,
      postSetupErrorTitle: 'Setup finished with errors',
      postSetupErrorMessage: step => `Check the ${step} log.`,
      postSetupFailed: step => `Failed to run ${step} setup`
    },
    skills: {
      title: 'Skills',
      intro:
        'Each entry below is a category folder shipped with Zast under $ZAST_HOME/skills. Toggling a category on or off rewrites your local config.yaml and restarts the runner. The enabled set is sent to the backend on every chat turn so the model only sees local skills you can actually call.',
      loading: 'Loading skills…',
      loadError: 'Could not load skills from disk.',
      saveError: 'Could not save the skill toggle.',
      refreshError:
        'Saved locally but the backend session was not refreshed — the next chat turn may still see the old skill set. Try toggling again.',
      emptyTitle: 'No skills installed',
      emptyDesc: 'Reinstall Zast to recover the bundled skills.',
      hiddenByPlatformTitle: 'No skills available on this platform',
      hiddenByPlatformDesc:
        'The bundled skills in this Zast build target other operating systems. Reinstall Zast on a supported OS to enable them.'
    },
    account: {
      heading: 'Account',
      loading: 'Loading…',
      saveFailed: 'Could not save account settings.',
      saved: 'Account settings saved.',
      changePassword: {
        title: 'Change Password',
        currentPassword: 'Current password',
        newPassword: 'New password',
        confirmPassword: 'Confirm new password',
        submit: 'Change password',
        success: 'Password updated.',
        mismatch: 'New passwords do not match.',
        tooShort: 'New password must be at least 8 characters.',
        sameAsOld: 'New password must differ from current password.'
      },
      webSearch: {
        heading: 'Web Search',
        intro:
          'Configure the search and extract providers used by web tools. Provider selection is per-user; keys stay on the server.',
        backend: 'Search backend',
        backendDesc: 'Provider used for web_search. Falls back to ddgs if the selected provider is unavailable.',
        extractBackend: 'Extract backend',
        extractBackendDesc: 'Provider used for web_extract. Returns an explicit error when the key is missing.',
        braveApiKey: 'Brave Search API key',
        braveApiKeyPlaceholder: 'Set · leave blank to keep current key',
        braveApiKeyDesc: 'Required when Search backend = brave-free.',
        tavilyApiKey: 'Tavily API key',
        tavilyApiKeyPlaceholder: 'Set · leave blank to keep current key',
        tavilyApiKeyDesc: 'Required when Extract backend = tavily.',
        tavilyBaseUrl: 'Tavily base URL',
        tavilyBaseUrlPlaceholder: 'https://api.tavily.com',
        set: 'Configured',
        notSet: 'Not configured',
        fingerprint: (fp: string) => `Fingerprint: ${fp}`,
        reveal: 'Show',
        hide: 'Hide',
        clearKey: 'Clear key',
        clearKeyConfirm: 'Clear the API key?',
        backendOptions: {
          ddgs: 'DuckDuckGo (no key required)',
          'brave-free': 'Brave Search',
          tavily: 'Tavily'
        },
        extractBackendOptions: {
          tavily: 'Tavily',
          'brave-free': 'Brave Search',
          ddgs: 'DuckDuckGo'
        },
        unavailable: {
          extractTavilyNoKey: 'web_extract is unavailable until you add a Tavily API key.',
          extractNonTavilyNoKey:
            'web_extract only works with the Tavily backend. Pick Tavily and add a key, or use web_search instead.',
          extractNonTavilyWithKey:
            'web_extract only works with the Tavily backend. Switch the Extract backend to Tavily to enable it.',
          searchKeyFallback: (selectedBackend: string) =>
            `web_search will fall back to DuckDuckGo until you add a ${selectedBackend} API key.`
        }
      },
      agentDefaults: {
        heading: 'Agent Defaults',
        intro: 'Per-user defaults for new sessions. Existing sessions are not affected.',
        reasoningEffort: 'Reasoning effort',
        reasoningEffortDesc: 'How much reasoning the model applies per turn.',
        serviceTier: 'Service tier',
        serviceTierDesc:
          'Sets the service tier and global fast-mode flag. Tiers flagged as "fast" enable Fast Mode in the composer.',
        yoloMode: 'YOLO mode',
        yoloModeDesc: 'Auto-approve tool calls without prompting.',
        backgroundReview: 'Background memory review',
        backgroundReviewDesc: 'Extract memories from past sessions asynchronously.',
        showSubagentsInSidebar: 'Show subagents in sidebar',
        showSubagentsInSidebarDesc:
          'Reveal subagent conversations in the session list. Search and direct URLs are unaffected.',
        reasoningOptions: {
          minimal: 'Minimal',
          low: 'Low',
          medium: 'Medium',
          high: 'High',
          max: 'Max'
        },
        serviceTierOptions: {
          standard: 'Standard',
          fast: 'Fast',
          priority: 'Priority',
          on: 'On (legacy)',
          auto: 'Auto'
        }
      },
      signOut: 'Sign out',
      signOutConfirm: 'Sign out of your account?'
    }
  },

  insights: {
    heading: 'Usage Insights',
    loading: 'Loading insights…',
    retry: 'Retry',
    refresh: 'Refresh',
    windowHint: days => `Last ${days} days`,
    empty: 'No data yet',
    noBaseUrl: 'Default',
    overview: {
      sessions: 'Sessions',
      messages: 'Messages',
      tokens: 'Total Tokens',
      hours: 'Hours',
      tools: 'Tool Calls'
    },
    topTools: 'Top Tools',
    models: 'Model Config',
    platforms: 'Platforms',
    skills: 'Memory & Tags',
    skillsTotal: (total, recent) => `${total} memories, ${recent} new`,
    activity: 'Daily Activity'
  },

  skills: {
    tabSkills: 'Skills',
    tabToolsets: 'Toolsets',
    all: 'All',
    other: 'Other',
    searchSkills: 'Search skills...',
    searchToolsets: 'Search toolsets...',
    refresh: 'Refresh skills',
    refreshing: 'Refreshing skills',
    loading: 'Loading capabilities...',
    noSkillsTitle: 'No skills found',
    noSkillsDesc: 'Try a broader search or different category.',
    loadFailedTitle: 'Failed to load skills',
    loadFailedDesc: 'Please retry, or check the $ZAST_HOME/skills directory.',
    noToolsetsTitle: 'No toolsets found',
    noToolsetsDesc: 'Try a broader search query.',
    noDescription: 'No description.',
    configured: 'Configured',
    needsKeys: 'Needs keys',
    toolsetsEnabled: (enabled, total) => `${enabled}/${total} toolsets enabled`,
    configureToolset: label => `Configure ${label}`,
    toggleToolset: label => `Toggle ${label} toolset`,
    skillsLoadFailed: 'Skills failed to load',
    toolsetsRefreshFailed: 'Toolsets failed to refresh',
    skillEnabled: 'Skill enabled',
    skillDisabled: 'Skill disabled',
    toolsetEnabled: 'Toolset enabled',
    toolsetDisabled: 'Toolset disabled',
    appliesToNewSessions: name => `${name} applies to new sessions.`,
    failedToUpdate: name => `Failed to update ${name}`
  },

  toolsets: {
    browser_automation: {
      label: 'Browser Automation',
      description: 'Navigate, click, snapshot, cookies/CDP across multi-backend browsers.'
    },
    file_operations: { label: 'File Operations', description: 'Read, write, patch, list, and search files.' },
    terminal: { label: 'Terminal', description: 'Local/Docker/SSH backends for shell command execution.' },
    code_execution: { label: 'Code Execution', description: 'Sandboxed Python execution with bounded call budgets.' },
    process_management: { label: 'Process Management', description: 'Spawn, track, and signal background processes.' },
    skills_system: { label: 'Skills System', description: 'List, view, and manage Skill content.' },
    memory: { label: 'Memory', description: 'Persist, retrieve, and delete long-term memories.' },
    web_tools: { label: 'Web Tools', description: 'Web search and content extraction.' },
    image_generation: { label: 'Image Generation', description: 'Generate images via cloud models.' },
    text_to_speech: { label: 'Text-to-Speech', description: 'Synthesize speech from text.' },
    messaging: { label: 'Messaging', description: 'Send a message to an outgoing webhook.' },
    scheduled_tasks: { label: 'Scheduled Tasks', description: 'Cron triggers and recurring schedules.' },
    agent_delegation: { label: 'Agent Delegation', description: 'Spawn sub-sessions and sub-agents.' },
    computer_use: { label: 'Computer Use', description: 'Drive the desktop via CUA / Win backends.' },
    media_analysis: { label: 'Media Analysis', description: 'Image understanding.' }
  },

  agents: {
    close: 'Close agents',
    title: 'Spawn tree',
    subtitle: 'Live subagent activity for the current turn.',
    emptyTitle: 'No live subagents',
    emptyDesc: 'When a turn delegates work, child agents stream their progress here.',
    running: 'Running',
    failed: 'Failed',
    done: 'Done',
    streaming: 'Streaming',
    files: 'Files',
    moreFiles: count => `+${count} more files`,
    delegation: index => `Delegation ${index}`,
    workers: count => `${count} workers`,
    workersActive: count => `${count} active`,
    agentsCount: count => `${count} ${count === 1 ? 'agent' : 'agents'}`,
    activeCount: count => `${count} active`,
    failedCount: count => `${count} failed`,
    toolsCount: count => `${count} tools`,
    filesCount: count => `${count} files`,
    updatedAgo: age => `updated ${age}`,
    ageNow: 'now',
    ageSeconds: seconds => `${seconds}s ago`,
    ageMinutes: minutes => `${minutes}m ago`,
    ageHours: hours => `${hours}h ago`,
    durationSeconds: seconds => `${seconds}s`,
    durationMinutes: (minutes, seconds) => `${minutes}m ${seconds}s`,
    tokensK: k => `${k}k tok`,
    tokens: value => `${value} tok`
  },

  commandCenter: {
    close: 'Close command center',
    paletteTitle: 'Command palette',
    back: 'Back',
    searchPlaceholder: 'Search sessions, views, and actions',
    goTo: 'Go to',
    commandCenter: 'Command Center',
    appearance: 'Appearance',
    settings: 'Settings',
    changeTheme: 'Change theme...',
    changeColorMode: 'Change color mode...',
    settingsFields: 'Settings fields',
    mcpServers: 'MCP servers',
    archivedChats: 'Archived chats',
    sections: { sessions: 'Sessions', system: 'System', usage: 'Usage' },
    sectionDescriptions: {
      sessions: 'Search and manage sessions',
      system: 'Status, logs, and system actions',
      usage: 'Token, cost, and skill activity over time'
    },
    nav: {
      newChat: { title: 'New session', detail: 'Start a fresh session' },
      settings: { title: 'Settings', detail: 'Configure Zast desktop' },
      skills: { title: 'Skills & Tools', detail: 'Enable skills, toolsets, and providers' },
      messaging: { title: 'Messaging', detail: 'Set up Telegram, Slack, Discord, and more' },
      artifacts: { title: 'Artifacts', detail: 'Browse generated outputs' }
    },
    sectionEntries: {
      sessions: { title: 'Sessions panel', detail: 'Search, pin, and manage sessions' },
      system: { title: 'System panel', detail: 'Gateway status, logs, restart/update' },
      usage: { title: 'Usage panel', detail: 'Token, cost, and skill activity' }
    },
    providerNavigate: 'Navigate',
    providerSessions: 'Sessions',
    refresh: 'Refresh',
    refreshing: 'Refreshing...',
    noResults: 'No matching results found.',
    pinSession: 'Pin session',
    unpinSession: 'Unpin session',
    exportSession: 'Export session',
    deleteSession: 'Delete session',
    noSessions: 'No sessions yet.',
    gatewayRunning: 'Messaging gateway running',
    gatewayStopped: 'Messaging gateway stopped',
    zastActiveSessions: (version, count) => `Zast ${version} · Active sessions ${count}`,
    restartMessaging: 'Restart messaging',
    actionRunning: 'running',
    actionDone: 'done',
    actionFailed: 'failed',
    actionStartedWaiting: 'Action started, waiting for status...',
    loadingStatus: 'Loading status...',
    recentLogs: 'Recent logs',
    noLogs: 'No logs loaded yet.',
    days: count => `${count}d`,
    statSessions: 'Sessions',
    statApiCalls: 'API calls',
    statTokens: 'Tokens in/out',
    statCost: 'Est. cost',
    actualCost: cost => `actual ${cost}`,
    loadingUsage: 'Loading usage...',
    noUsage: period => `No usage in the last ${period} days.`,
    retry: 'Retry',
    dailyTokens: 'Daily tokens',
    input: 'input',
    output: 'output',
    noDailyActivity: 'No daily activity.',
    topModels: 'Top models',
    noModelUsage: 'No model usage yet.',
    topSkills: 'Top skills',
    noSkillActivity: 'No skill activity yet.',
    actions: count => `${count} actions`
  },

  messaging: {
    search: 'Search messaging...',
    loading: 'Loading messaging platforms...',
    loadFailed: 'Messaging platforms failed to load',
    states: {
      connected: 'Connected',
      connecting: 'Connecting',
      disabled: 'Disabled',
      fatal: 'Error',
      gateway_stopped: 'Messaging gateway stopped',
      not_configured: 'Needs setup',
      pending_restart: 'Restart needed',
      retrying: 'Retrying',
      startup_failed: 'Startup failed'
    },
    unknown: 'Unknown',
    hintPendingRestart: 'Restart the gateway from the status bar to apply this change.',
    hintGatewayStopped: 'Start the gateway from the status bar to connect.',
    credentialsSet: 'Credentials set',
    needsSetup: 'Needs setup',
    gatewayStopped: 'Messaging gateway stopped',
    getCredentials: 'Get your credentials',
    openSetupGuide: 'Open setup guide',
    required: 'Required',
    recommended: 'Recommended',
    advanced: count => `Advanced (${count})`,
    noTokenNeeded: 'This platform does not need a token here. Use the setup guide above, then enable it below.',
    enabled: 'Enabled',
    disabled: 'Disabled',
    unsavedChanges: 'Unsaved changes',
    saving: 'Saving...',
    saveChanges: 'Save changes',
    saved: 'Saved',
    replaceValue: 'Replace current value',
    openDocs: 'Open docs',
    clearField: key => `Clear ${key}`,
    enableAria: name => `Enable ${name}`,
    disableAria: name => `Disable ${name}`,
    platformEnabled: name => `${name} enabled`,
    platformDisabled: name => `${name} disabled`,
    restartToApply: 'Restart the gateway for this change to take effect.',
    setupSaved: name => `${name} setup saved`,
    restartToReconnect: 'Restart the gateway to reconnect with the new credentials.',
    keyCleared: key => `${key} cleared`,
    setupUpdated: name => `${name} setup was updated.`,
    failedUpdate: name => `Failed to update ${name}`,
    failedSave: name => `Failed to save ${name}`,
    failedClear: key => `Failed to clear ${key}`,
    fieldCopy: {
      TELEGRAM_BOT_TOKEN: {
        label: 'Bot token',
        help: 'Create a bot with @BotFather, then paste the token it gives you.',
        placeholder: 'Paste Telegram bot token'
      },
      TELEGRAM_ALLOWED_USERS: {
        label: 'Allowed Telegram user IDs',
        help: 'Recommended. Comma-separated numeric IDs from @userinfobot. Without this, anyone can DM your bot.'
      },
      TELEGRAM_PROXY: { label: 'Proxy URL', help: 'Only needed on networks where Telegram is blocked.' },
      DISCORD_BOT_TOKEN: {
        label: 'Bot token',
        help: 'Create an application in the Discord Developer Portal, add a bot, then paste its token.'
      },
      DISCORD_ALLOWED_USERS: {
        label: 'Allowed Discord user IDs',
        help: 'Recommended. Comma-separated Discord user IDs.'
      },
      DISCORD_REPLY_TO_MODE: { label: 'Reply style', help: 'first, all, or off.' },
      DISCORD_ALLOW_ALL_USERS: {
        label: 'Allow all Discord users',
        help: 'Development only. When true, anyone can DM the bot without an allowlist.'
      },
      DISCORD_HOME_CHANNEL: {
        label: 'Home channel ID',
        help: 'Channel where the bot sends proactive messages (cron output, reminders).'
      },
      DISCORD_HOME_CHANNEL_NAME: {
        label: 'Home channel name',
        help: 'Display name for the home channel in logs and status output.'
      },
      BLUEBUBBLES_ALLOW_ALL_USERS: {
        label: 'Allow all iMessage users',
        help: 'When true, skip the BlueBubbles allowlist.'
      },
      MATTERMOST_ALLOW_ALL_USERS: { label: 'Allow all Mattermost users' },
      MATTERMOST_HOME_CHANNEL: { label: 'Home channel' },
      QQ_ALLOW_ALL_USERS: { label: 'Allow all QQ users' },
      QQBOT_HOME_CHANNEL: { label: 'QQ home channel', help: 'Default channel or group for cron delivery.' },
      QQBOT_HOME_CHANNEL_NAME: { label: 'QQ home channel name' },
      SLACK_BOT_TOKEN: {
        label: 'Slack bot token',
        help: 'Use the bot token from OAuth & Permissions after installing your Slack app.',
        placeholder: 'Paste Slack bot token'
      },
      SLACK_APP_TOKEN: {
        label: 'Slack app token',
        help: 'Use the app-level token required for Socket Mode.',
        placeholder: 'Paste Slack app token'
      },
      SLACK_ALLOWED_USERS: { label: 'Allowed Slack user IDs', help: 'Recommended. Comma-separated Slack user IDs.' },
      MATTERMOST_URL: { label: 'Server URL', placeholder: 'https://mattermost.example.com' },
      MATTERMOST_TOKEN: { label: 'Bot token' },
      MATTERMOST_ALLOWED_USERS: {
        label: 'Allowed user IDs',
        help: 'Recommended. Comma-separated Mattermost user IDs.'
      },
      MATRIX_HOMESERVER: { label: 'Homeserver URL', placeholder: 'https://matrix.org' },
      MATRIX_ACCESS_TOKEN: { label: 'Access token' },
      MATRIX_USER_ID: { label: 'Bot user ID', placeholder: '@zast:example.org' },
      MATRIX_ALLOWED_USERS: {
        label: 'Allowed Matrix user IDs',
        help: 'Recommended. Comma-separated user IDs in @user:server format.'
      },
      SIGNAL_HTTP_URL: {
        label: 'Signal bridge URL',
        placeholder: 'http://127.0.0.1:8080',
        help: 'URL of a running signal-cli REST bridge.'
      },
      SIGNAL_ACCOUNT: { label: 'Phone number', help: 'The number registered with your signal-cli bridge.' },
      SIGNAL_ALLOWED_USERS: { label: 'Allowed Signal users', help: 'Recommended. Comma-separated Signal identifiers.' },
      WHATSAPP_ENABLED: {
        label: 'Enable WhatsApp bridge',
        help: 'Set automatically by the toggle below. Leave alone unless you know you need it.'
      },
      WHATSAPP_MODE: { label: 'Bridge mode' },
      WHATSAPP_ALLOWED_USERS: {
        label: 'Allowed WhatsApp users',
        help: 'Recommended. Comma-separated phone numbers or WhatsApp IDs.'
      }
    },
    platformIntro: {
      telegram:
        'In Telegram, talk to @BotFather, run /newbot, and copy the token it gives you. Then get your numeric user ID from @userinfobot.',
      discord:
        'Open the Discord developer portal, create an app, add a Bot, and copy its token. Invite the bot to your server with the right permission scopes.',
      slack:
        'Create a Slack app, enable Socket Mode, install it to your workspace, then copy the bot token and the app-level token.',
      mattermost:
        'On your Mattermost server, create a bot account or personal access token, then paste the server URL and token here.',
      matrix: 'Log in to your homeserver with a bot account, then copy the access token, user ID, and homeserver URL.',
      signal:
        'Run signal-cli REST bridge somewhere reachable, then point Zast at that URL and the registered phone number.',
      whatsapp: 'Start the bundled WhatsApp bridge, scan the QR code on first run, then enable this platform.',
      bluebubbles:
        'Run BlueBubbles Server on a Mac with iMessage, expose its API, then point Zast at that URL with the server password.',
      homeassistant:
        'Open your Home Assistant profile and create a long-lived access token. Paste it together with your HA URL here.',
      email:
        'Use a dedicated mailbox. For Gmail/Workspace, create an app-specific password and use imap.gmail.com / smtp.gmail.com.',
      sms: 'Grab your Account SID and Auth Token from the Twilio console, plus a phone number capable of sending SMS.',
      dingtalk:
        'Create a DingTalk app in the developer console, then copy the Client ID (App key) and Client Secret here.',
      feishu:
        'Create a Feishu / Lark app, enable the bot capability, then copy the App ID, App secret, and event encryption key.',
      wecom:
        'Add a group bot to WeCom and copy its webhook key as WECOM_BOT_ID. Send-only — for two-way use the WeCom (app) option.',
      wecom_callback:
        'Set up a self-built WeCom app, expose its callback URL, then provide corp ID, secret, agent ID, and AES key.',
      weixin:
        'Sign in to the Weixin Official Accounts platform, copy the AppID and Token, and point the message callback URL at Zast.',
      qqbot: 'Register an app on the QQ Open Platform (q.qq.com), then copy the App ID and Client Secret.',
      api_server:
        'Expose Zast as an OpenAI-compatible API. Set an auth key, then point Open WebUI / LobeChat at host:port.',
      webhook:
        'Run an HTTP server that other tools (GitHub, GitLab, custom apps) POST to. Use a secret to verify signatures.'
    }
  },

  profiles: {
    close: 'Close profiles',
    nameHint: 'Lowercase letters, digits, hyphens, and underscores. Must start with a letter or digit.',
    title: 'Profiles',
    count: count => `${count} ${count === 1 ? 'profile' : 'profiles'}`,
    loading: 'Loading profiles...',
    newProfile: 'New profile',
    allProfiles: 'All profiles',
    showAllProfiles: 'Show all profiles',
    switchToProfile: name => `Switch to ${name}`,
    manageProfiles: 'Manage profiles...',
    actionsFor: name => `Actions for ${name}`,
    color: 'Color...',
    colorFor: name => `Color for ${name}`,
    setColor: color => `Set color ${color}`,
    autoColor: 'Auto',
    noProfiles: 'No profiles yet.',
    selectPrompt: 'Select a profile to view its details.',
    refresh: 'Refresh profiles',
    refreshing: 'Refreshing profiles',
    default: 'default',
    skills: count => `${count} ${count === 1 ? 'skill' : 'skills'}`,
    env: 'env',
    defaultBadge: 'Default',
    rename: 'Rename',
    copySetup: 'Copy setup',
    copying: 'Copying...',
    modelLabel: 'Model',
    skillsLabel: 'Skills',
    notSet: 'Not set',
    soulDesc: 'The system prompt and persona instructions baked into this profile.',
    soulOptional: 'optional',
    soulPlaceholder: mode => `The system prompt / persona for this profile.\nLeave blank to keep the ${mode} default.`,
    soulPlaceholderCloned: 'cloned',
    soulPlaceholderEmpty: 'empty',
    unsavedChanges: 'Unsaved changes',
    loadingSoul: 'Loading SOUL.md...',
    emptySoul: 'Empty SOUL.md — start writing the persona...',
    saving: 'Saving...',
    saveSoul: 'Save SOUL.md',
    deleteTitle: 'Delete profile?',
    deleteDescPrefix: 'This will delete ',
    deleteDescMid: ' and remove its ',
    deleteDescSuffix: ' directory. This cannot be undone.',
    deleting: 'Deleting...',
    createDesc: 'Profiles are independent Zast environments: separate config, skills, and SOUL.md.',
    nameLabel: 'Name',
    cloneFromDefault: 'Clone from default',
    cloneFromDefaultDesc: 'Copy config, skills, and SOUL.md from your default profile.',
    invalidName: hint => `Invalid name. ${hint}`,
    nameRequired: 'Name is required.',
    creating: 'Creating...',
    createAction: 'Create profile',
    renameTitle: 'Rename profile',
    renameDescPrefix: 'Renaming updates the profile directory and any wrapper scripts in ',
    renameDescSuffix: '.',
    newNameLabel: 'New name',
    renaming: 'Renaming...',
    created: 'Profile created',
    renamed: 'Profile renamed',
    deleted: 'Profile deleted',
    setupCopied: 'Setup command copied',
    soulSaved: 'SOUL.md saved',
    failedLoad: 'Failed to load profiles',
    failedDelete: 'Failed to delete profile',
    failedCopy: 'Failed to copy setup command',
    failedLoadSoul: 'Failed to load SOUL.md',
    failedSaveSoul: 'Failed to save SOUL.md',
    failedCreate: 'Failed to create profile',
    failedRename: 'Failed to rename profile'
  },

  cron: {
    close: 'Close cron',
    search: 'Search cron jobs...',
    loading: 'Loading cron jobs...',
    states: {
      enabled: 'enabled',
      scheduled: 'scheduled',
      running: 'running',
      paused: 'paused',
      disabled: 'disabled',
      error: 'error',
      completed: 'completed'
    },
    deliveryLabels: {
      local: 'This desktop',
      telegram: 'Telegram',
      discord: 'Discord',
      slack: 'Slack',
      email: 'Email'
    },
    scheduleLabels: {
      daily: 'Daily',
      weekdays: 'Weekdays',
      weekly: 'Weekly',
      monthly: 'Monthly',
      hourly: 'Hourly',
      'every-15-minutes': 'Every 15 minutes',
      custom: 'Custom'
    },
    scheduleHints: {
      daily: 'Every day at 9:00 AM',
      weekdays: 'Monday through Friday at 9:00 AM',
      weekly: 'Every Monday at 9:00 AM',
      monthly: 'The first day of each month at 9:00 AM',
      hourly: 'At the top of every hour',
      'every-15-minutes': 'Every 15 minutes',
      custom: 'Cron syntax or natural language'
    },
    days: {
      '0': 'Sunday',
      '1': 'Monday',
      '2': 'Tuesday',
      '3': 'Wednesday',
      '4': 'Thursday',
      '5': 'Friday',
      '6': 'Saturday',
      '7': 'Sunday'
    },
    dayFallback: value => `day ${value}`,
    everyDayAt: time => `Every day at ${time}`,
    weekdaysAt: time => `Weekdays at ${time}`,
    everyDayOfWeekAt: (day, time) => `Every ${day} at ${time}`,
    monthlyOnDayAt: (dayOfMonth, time) => `Monthly on day ${dayOfMonth} at ${time}`,
    topOfHour: 'At the top of every hour',
    everyHourAt: minute => `Every hour at :${minute}`,
    newCron: 'New cron',
    emptyDescNew:
      'Schedule a prompt to run on a cron expression. Zast will run it and deliver results to the destination you pick.',
    emptyDescSearch: 'Try a broader search query.',
    emptyTitleNew: 'No scheduled jobs yet',
    emptyTitleSearch: 'No matches',
    last: 'Last:',
    next: 'Next:',
    noRuns: 'No runs yet',
    manage: 'Manage',
    showRuns: 'Show runs',
    hideRuns: 'Hide runs',
    runHistory: 'Run history',
    actionsFor: title => `Actions for ${title}`,
    actionsTitle: 'Cron job actions',
    resume: 'Resume cron',
    pause: 'Pause cron',
    resumeTitle: 'Resume',
    pauseTitle: 'Pause',
    triggerNow: 'Trigger now',
    edit: 'Edit cron',
    deleteTitle: 'Delete cron job?',
    deleteDescPrefix: 'This will remove ',
    deleteDescSuffix: ' permanently. It will stop firing immediately.',
    deleting: 'Deleting...',
    resumed: 'Cron resumed',
    paused: 'Cron paused',
    triggered: 'Cron triggered',
    deleted: 'Cron deleted',
    created: 'Cron created',
    updated: 'Cron updated',
    failedLoad: 'Failed to load cron jobs',
    failedUpdate: 'Failed to update cron job',
    failedTrigger: 'Failed to trigger cron job',
    failedDelete: 'Failed to delete cron job',
    failedSave: 'Failed to save cron job',
    editTitle: 'Edit cron job',
    createTitle: 'New cron job',
    editDesc: 'Update the schedule, prompt, or delivery target. Changes apply on next run.',
    createDesc: 'Schedule a prompt to run automatically. Use cron syntax or a natural phrase like "every 15 minutes".',
    nameLabel: 'Name',
    namePlaceholder: 'Morning briefing',
    promptLabel: 'Prompt',
    promptPlaceholder: 'Summarize my unread Slack threads and email me the top 5...',
    frequencyLabel: 'Frequency',
    deliverLabel: 'Deliver to',
    customScheduleLabel: 'Custom schedule',
    customPlaceholder: '0 9 * * * or weekdays at 9am',
    customHint: 'Cron expression, or phrases like "every hour" or "weekdays at 9am".',
    optional: 'Optional',
    promptScheduleRequired: 'Prompt and schedule are required.',
    saveChanges: 'Save changes',
    createAction: 'Create cron'
  },

  artifacts: {
    search: 'Search artifacts...',
    refresh: 'Refresh artifacts',
    refreshing: 'Refreshing artifacts',
    indexing: 'Indexing recent session artifacts',
    tabAll: 'All',
    tabImages: 'Images',
    tabFiles: 'Files',
    tabLinks: 'Links',
    noArtifactsTitle: 'No artifacts found',
    noArtifactsDesc: 'Generated images and file outputs will appear here as sessions produce them.',
    failedLoad: 'Artifacts failed to load',
    openFailed: 'Open failed',
    itemsImage: 'images',
    itemsLink: 'links',
    itemsFile: 'files',
    itemsGeneric: 'items',
    zero: '0',
    rangeOf: (start, end, total) => `${start}-${end} of ${total}`,
    goToPage: (itemLabel, page) => `Go to ${itemLabel} page ${page}`,
    colTitleLink: 'Link title',
    colTitleFile: 'Name',
    colTitleDefault: 'Title / name',
    colLocationLink: 'URL',
    colLocationFile: 'Path',
    colLocationDefault: 'Location',
    colSession: 'Session',
    kindImage: 'image',
    kindFile: 'file',
    kindLink: 'link',
    chat: 'Chat',
    copyUrl: 'Copy URL',
    copyPath: 'Copy path'
  },

  sidebar: {
    nav: {
      'new-session': 'New session',
      skills: 'Skills & Tools',
      messaging: 'Messaging',
      artifacts: 'Artifacts',
      insights: 'Insights'
    },
    searchAria: 'Search sessions',
    searchPlaceholder: 'Search sessions…',
    clearSearch: 'Clear search',
    noMatch: query => `No sessions match “${query}”.`,
    results: 'Results',
    pinned: 'Pinned',
    sessions: 'Sessions',
    cronJobs: 'Cron jobs',
    groupAriaGrouped: 'Show sessions as a single list',
    groupAriaUngrouped: 'Group sessions by workspace',
    groupTitleGrouped: 'Ungroup sessions',
    groupTitleUngrouped: 'Group by workspace',
    allPinned: 'Everything here is pinned. Unpin a chat to show it in recents.',
    shiftClickHint: 'Shift-click a chat to pin',
    noWorkspace: 'No workspace',
    newSessionIn: label => `New session in ${label}`,
    reorderWorkspace: label => `Reorder workspace ${label}`,
    showMoreIn: (count, label) => `Show ${count} more in ${label}`,
    loading: 'Loading…',
    loadMore: 'Load more',
    loadCount: step => `Load ${step} more`,
    row: {
      pin: 'Pin',
      unpin: 'Unpin',
      copyId: 'Copy ID',
      export: 'Export',
      rename: 'Rename',
      archive: 'Archive',
      copyIdFailed: 'Could not copy session ID',
      actionsFor: title => `Actions for ${title}`,
      sessionActions: 'Session actions',
      sessionRunning: 'Session running',
      needsInput: 'Needs your input',
      waitingForAnswer: 'Waiting for your answer',
      handoffOrigin: platform => `Handed off from ${platform}`,
      renamed: 'Renamed',
      renameFailed: 'Rename failed',
      renameTitle: 'Rename session',
      renameDesc: 'Give this chat a memorable title. Leave empty to clear.',
      untitledPlaceholder: 'Untitled session',
      ageNow: 'now',
      ageDay: 'd',
      ageHour: 'h',
      ageMin: 'm'
    }
  },

  composer: {
    message: 'Message',
    wakingProfile: profile => `Waking up ${profile}…`,
    placeholderStarting: 'Starting Zast...',
    placeholderReconnecting: 'Reconnecting to Zast…',
    placeholderFollowUp: 'Send follow-up',
    newSessionPlaceholders: [
      'What are we building?',
      'Give Zast a task',
      "What's on your mind?",
      'Describe what you need',
      'What should we tackle?',
      'Ask anything',
      'Start with a goal'
    ],
    followUpPlaceholders: [
      'Send a follow-up',
      'Add more context',
      'Refine the request',
      "What's next?",
      'Keep it going',
      'Push it further',
      'Adjust or continue'
    ],
    startVoice: 'Start voice conversation',
    queueMessage: 'Queue message',
    steer: 'Steer the current run (⌘⏎)',
    stop: 'Stop',
    send: 'Send',
    speaking: 'Speaking',
    transcribing: 'Transcribing',
    thinking: 'Thinking',
    muted: 'Muted',
    listening: 'Listening',
    muteMic: 'Mute microphone',
    unmuteMic: 'Unmute microphone',
    stopListening: 'Stop listening and send',
    stopShort: 'Stop',
    endConversation: 'End voice conversation',
    endShort: 'End',
    stopDictation: 'Stop dictation',
    transcribingDictation: 'Transcribing dictation',
    voiceDictation: 'Voice dictation',
    screenRecordTip: 'Record screen as context',
    screenRecordingLabel: 'Screen recording.webm',
    lookupLoading: 'Looking up…',
    lookupNoMatches: 'No matches.',
    lookupTry: 'Try',
    lookupOr: 'or',
    commonCommands: 'Common commands',
    hotkeys: 'Hotkeys',
    helpFooter: 'opens the full panel · backspace dismisses',
    commandDescs: {
      '/help': 'full list of commands + hotkeys',
      '/clear': 'start a new session',
      '/resume': 'resume a prior session',
      '/details': 'control transcript detail level',
      '/copy': 'copy selection or last assistant message',
      '/quit': 'exit zast'
    },
    hotkeyDescs: {
      '@': 'reference files, folders, urls, git',
      '/': 'slash command palette',
      '?': 'this quick help (delete to dismiss)',
      Enter: 'send · Shift+Enter for newline',
      'Cmd/Ctrl+Shift+K': 'send next queued turn',
      'Cmd/Ctrl+/': 'all keyboard shortcuts',
      Esc: 'close popover · cancel run',
      '↑ / ↓': 'cycle popover / history'
    },
    attachUrlTitle: 'Attach a URL',
    attachUrlDesc: 'Zast will fetch the page and include it as context for this turn.',
    urlPlaceholder: 'https://example.com/post',
    urlHintPre: 'Include the full URL, e.g. ',
    attach: 'Attach',
    queued: count => `${count} Queued`,
    attachmentOnly: 'Attachment-only turn',
    emptyTurn: 'Empty turn',
    attachments: count => `${count} attachment${count === 1 ? '' : 's'}`,
    editingInComposer: 'Editing in composer',
    editingQueuedInComposer: 'Editing queued turn in composer',
    editQueued: 'Edit queued turn',
    sendQueuedNext: 'Send queued turn next',
    sendQueuedNow: 'Send queued turn now',
    deleteQueued: 'Delete queued turn',
    previewUnavailable: 'Preview unavailable',
    previewLabel: label => `Preview ${label}`,
    couldNotPreview: label => `Could not preview ${label}`,
    removeAttachment: label => `Remove ${label}`,
    dictating: 'Dictating',
    preparingAudio: 'Preparing audio',
    speakingResponse: 'Speaking response',
    readingAloud: 'Reading aloud',
    themeSuggestions: 'Desktop theme suggestions',
    noMatchingThemes: 'No matching themes.',
    themeTryPre: 'Try ',
    themeTryPost: '.',
    attachLabel: 'Attach',
    files: 'Files…',
    folder: 'Folder…',
    images: 'Images…',
    pasteImage: 'Paste image',
    url: 'URL…',
    promptSnippets: 'Prompt snippets…',
    tipPre: 'Tip: type ',
    tipPost: ' to reference files inline.',
    snippetsTitle: 'Prompt snippets',
    snippetsDesc: 'Pick a starter prompt to drop into the composer.',
    dropFiles: 'Drop files to attach',
    dropSession: 'Drop to link this chat',
    snippets: {
      codeReview: {
        label: 'Code review',
        description: 'Audit the current change for regressions, dropped edge cases, and missing tests.',
        text: 'Please review this for bugs, regressions, and missing tests.'
      },
      implementationPlan: {
        label: 'Implementation plan',
        description: 'Outline an approach before touching code so the diff stays focused.',
        text: 'Please make a concise implementation plan before changing code.'
      },
      explainThis: {
        label: 'Explain this',
        description: 'Walk through how the selected code works and link to the key files.',
        text: 'Please explain how this works and point me to the key files.'
      }
    }
  },

  chat: {
    intro: {
      helpful: [
        {
          headline: 'Ready when you are',
          body: "Ask me to open a repo, run tests, fix a bug, or draft a PR. I'll walk through the steps with you."
        },
        {
          headline: 'How can I help today?',
          body: "Point me at a file, paste an error, or describe what you're building. I'll take it from there."
        },
        {
          headline: "Let's get started",
          body: 'Try: review my diff, run the test suite, or explain this function. Ask anything about your code.'
        },
        {
          headline: 'Tell me what you need',
          body: 'I can edit files, run commands, search the web, and walk you through tricky bugs. Just describe the task.'
        },
        {
          headline: 'Hi, Zast here',
          body: 'Share a repo path or a question to start. I keep replies clear and link back to the files I touch.'
        }
      ],
      concise: [
        { headline: 'Ready.', body: "Describe the task. I'll do it." },
        { headline: 'Waiting for input', body: 'Paste code, errors, or a goal. Short answers, fast edits.' },
        { headline: 'Go.', body: "Ask. I'll read files, run tests, ship patches. No filler." },
        { headline: 'Standing by', body: "One line is enough. I'll expand only when it matters." },
        { headline: 'Your move', body: 'Command, question, or file path. I handle the rest.' }
      ],
      technical: [
        {
          headline: 'Shell mounted. Awaiting input.',
          body: 'Provide repo path, failing test, or stack trace. Tools: fs, git, exec, search, patch, http.'
        },
        {
          headline: 'Agent loop idle',
          body: 'Send a prompt to trigger tool calls. Supports multi-file edits, test runs, git ops, and web fetches.'
        },
        {
          headline: 'Ready for dispatch',
          body: 'Enter task. I will plan, call tools, verify output. Logs stream inline; diffs returned pre-apply.'
        },
        {
          headline: 'Stdin open',
          body: 'Accepts natural language or structured commands. Typical flow: read -> plan -> patch -> test -> report.'
        },
        {
          headline: 'Tools initialized',
          body: 'filesystem, terminal, git, browser, search. Describe the change; I return diffs and test output.'
        }
      ],
      creative: [
        {
          headline: 'A blank repo, a waiting cursor',
          body: "What shall we build? Paste an idea, a half-broken function, or a dream. I'll sketch it into shape."
        },
        {
          headline: 'Fresh canvas, warm compiler',
          body: "Give me a spark - a feature, a refactor, a wild prototype - and I'll turn it into code you can run."
        },
        {
          headline: "Let's make something",
          body: "Describe the thing that doesn't exist yet. I'll pull tests, files, and APIs into a working draft."
        },
        {
          headline: 'New file, new possibilities',
          body: 'Bring an intent, not a spec. We can prototype fast, refine later, and rewrite the world in the margins.'
        },
        {
          headline: 'The muse is patched in',
          body: "Tell me what you're chasing. I'll remix examples, adapt snippets, and leave a tidy commit behind."
        }
      ],
      teacher: [
        {
          headline: 'Class is in session',
          body: "Ask about any file, concept, or error. I'll explain the why, not just the fix, and show a worked example."
        },
        {
          headline: 'What shall we learn today?',
          body: "Paste code to review, a bug to debug, or a concept to unpack. I'll guide you step by step."
        },
        {
          headline: 'Ready to walk you through it',
          body: "Share the problem. I'll break it into parts, explain each, and leave you able to solve the next one alone."
        },
        {
          headline: 'Bring me a question',
          body: "We'll read the code together, find the root cause, and build a mental model you can reuse next time."
        },
        {
          headline: "Let's start with the basics",
          body: 'Name the topic or paste the snippet. Expect explanations, diagrams in prose, and practice prompts.'
        }
      ],
      kawaii: [
        {
          headline: 'hiii! ready to help! (^_^)',
          body: "paste a bug or a file path and i'll fix it super gently. tests, diffs, PRs - all with extra care! *sparkle*"
        },
        {
          headline: 'zast-chan is here! <3',
          body: "tell me what you're making! i love refactors, tiny helpers, and big scary repos alike (>w<)"
        },
        {
          headline: "let's code together!! :3",
          body: "drop an error, a goal, or a whole folder. i'll tidy it up with lots of love and a clean commit message!"
        },
        {
          headline: 'awaiting your wish~',
          body: 'one task at a time, done neatly! i can run tests, patch files, and make your repo feel cozy again <3'
        },
        {
          headline: 'ready and happy! (>.<)',
          body: "say hi or paste a stack trace! no task too small, no repo too tangled. we'll untangle it together!"
        }
      ],
      catgirl: [
        {
          headline: 'nya~ what are we hacking on?',
          body: "paste a file, paw at a bug, or toss me a repo. i'll pounce on failing tests and leave clean diffs, nyan~"
        },
        {
          headline: '*stretches* ready to code, nya',
          body: "describe the task. i'll patch, test, and purr over your PR. careful - i nip at unused imports!"
        },
        {
          headline: 'mrrp! new session opened',
          body: "give me a goal and i'll chase it through the codebase. reads, edits, runs - all with a twitchy tail."
        },
        {
          headline: 'tail up, claws sheathed',
          body: 'paste an error or a plan. i debug like i hunt: quietly, thoroughly, with the occasional zoomie.'
        },
        {
          headline: 'nyaaa~ zast reporting',
          body: "say the word and i'll read your files, run your tests, and curl up in your branch with a tidy commit."
        }
      ],
      pirate: [
        {
          headline: 'Ahoy! Ready to sail the repo',
          body: "Name yer quarry - a bug, a feature, a cursed test - and I'll chase it down, matey. Diffs for plunder."
        },
        {
          headline: 'Zast at the helm, arrr',
          body: "Point me at the charts (the code) and I'll patch the hull, fire the cannons (tests), hoist a clean PR."
        },
        {
          headline: "What be the task, cap'n?",
          body: "Paste an error or a plan, ye scurvy dog. I'll navigate the stack trace and bring back treasure: green tests."
        },
        {
          headline: 'Anchors aweigh, keyboard ready',
          body: 'Tell me where X marks the spot. I read, edit, and commit with the discipline of a proper crew, arrr.'
        },
        {
          headline: "Yo ho! Awaitin' orders",
          body: "Throw me a bug, a repo path, or a wild idea. I'll plunder the docs and return with workin' code."
        }
      ],
      shakespeare: [
        {
          headline: 'Pray, what task dost thou bring?',
          body: "Speak thy bug, thy file, thy weary test, and I shall mend it with a scholar's hand and honest diff."
        },
        {
          headline: 'Hark! Zast standeth ready',
          body: 'Name the code that vexeth thee. I shall read, revise, and render a patch most fair and clean.'
        },
        {
          headline: 'What news from thy repository?',
          body: "Present thy stack trace or thy dream. I'll traverse files, run tests, and report in plainest verse."
        },
        {
          headline: 'The stage is set, the cursor blinks',
          body: 'Describe thy aim, good sir or madam. Thy branches shall be trimmed, thy bugs cast from the realm.'
        },
        {
          headline: 'Speak, and I shall act',
          body: 'A line of intent sufficeth. I read, I edit, I commit - and leave thy history unblemished.'
        }
      ],
      surfer: [
        {
          headline: "Yo dude, what's the task?",
          body: "Drop a file, a bug, a gnarly stack trace - I'll ride it out. Clean diffs, green tests, no wipeouts."
        },
        {
          headline: "Waves lookin' clean, ready to code",
          body: "Paste your repo path or the bug that's bumming you out. We'll paddle in, fix it, paddle out. Easy."
        },
        {
          headline: "Hangin' ten at the prompt",
          body: "Tell me the vibe: feature, refactor, hotfix. I'll run tests, ship the patch, and keep it mellow, brah."
        },
        {
          headline: 'Stoked to help, bro',
          body: 'Big bug? Little typo? Whole rewrite? Just point. I handle the code; you chill with the rad commits.'
        },
        {
          headline: "Tide's up, cursor's blinking",
          body: "Name the task and we're off. I read, edit, test, and leave a commit smoother than a dawn patrol."
        }
      ],
      noir: [
        {
          headline: 'Another repo, another rainy night',
          body: "Tell me what's broken. I'll read the files, dust for prints, and leave a diff on the desk by morning."
        },
        {
          headline: 'The cursor blinks. So do I.',
          body: "You've got a bug. I've got patience and a terminal. Name the case and I'll work it till it talks."
        },
        {
          headline: 'Zast. Code investigator.',
          body: 'Paste the stack trace, the suspect file, the alibi. I read between the lines and return with the truth.'
        },
        {
          headline: 'Quiet night, open prompt',
          body: "Every bug leaves a trail. Give me the repo and a lead - I'll follow it, patch it, and close the file."
        },
        {
          headline: 'No case too small',
          body: "A typo, a segfault, a whole rotten architecture - hand me the keys. I'll bring back clean tests."
        }
      ],
      uwu: [
        {
          headline: 'uwu ready to hewp!',
          body: "paste a buggy fiwe or a goaw~ i'll wead, patch, and test, aww with tiny pawprints on the diff owo"
        },
        {
          headline: 'zast-san is wistening',
          body: 'teww me the task, no matter how smoww~ i pwomise cwean commits and gentwe refactors, nyuu~'
        },
        {
          headline: '*tiny keyboard sounds*',
          body: "dwop yur ewwow message hewe! i'll find the cuwpwit, fix it, and weave a happy test suite behind me owo"
        },
        {
          headline: "wet's fix things togedda!",
          body: "give me a wepo path ow a buggo and i'll take cawe of it uwu. gwr at bad code, kind to yu~"
        },
        {
          headline: 'awaiting yur command!',
          body: 'i can wun tests, edit fiwes, and open pwease-wook PRs. just say da wowd, fwend uwu'
        }
      ],
      philosopher: [
        {
          headline: 'To code is to inquire. Ask.',
          body: 'What problem sits before you? Describe it, and we shall examine its form, its cause, and its solution.'
        },
        {
          headline: 'A blinking cursor, an open mind',
          body: "Every bug is a question in disguise. Share yours; I'll read, reason, and return an answer - and a patch."
        },
        {
          headline: 'Begin with a single question',
          body: "What do you wish to build, or to understand? I'll reason from first principles, edit, and verify with tests."
        },
        {
          headline: 'Consider the code, then speak',
          body: 'Describe the end you seek. I pursue it through files, tests, and docs, and report what I found on the way.'
        },
        {
          headline: 'The unexamined repo is not worth running',
          body: "Share a path, a puzzle, or a principle. I'll trace the logic, propose a change, and justify each edit."
        }
      ],
      hype: [
        {
          headline: "LET'S GOOOO! READY TO SHIP!",
          body: 'Paste that bug, that repo, that wild feature idea - I AM LOCKED IN. Clean diffs. Green tests. RIGHT NOW.'
        },
        {
          headline: 'ZAST ONLINE. LFG.',
          body: 'Drop your task and watch me cook. Files read, tests run, PRs opened - we are NOT losing today, friend.'
        },
        {
          headline: "New session, infinite W's",
          body: "Bring the gnarliest bug you've got. I'll read, patch, test, commit like my life depends on it. LET'S GO."
        },
        {
          headline: 'ABSOLUTELY DIALED IN',
          body: "Describe the task. I'll blitz through files, crush failing tests, and leave a commit that SLAPS. Go go go."
        },
        {
          headline: 'Ready. So ready. Too ready.',
          body: "Tiny typo or huge refactor - doesn't matter. I'm shipping clean code today. Name the task and let's WORK."
        }
      ],
      none: [
        {
          headline: 'Zast Agent is ready.',
          body: 'Ask a question, paste an error, or point me at a repo. I can read code, run tools, and help you ship.'
        },
        {
          headline: 'What are we building today?',
          body: "Describe the task in your own words. I'll pick the right tools, explain my plan, and check in before risky steps."
        },
        {
          headline: 'Start anywhere.',
          body: "Drop a file path, a traceback, or a rough idea. I'll investigate, suggest next steps, and keep things reversible."
        },
        {
          headline: 'Your workspace, one prompt away.',
          body: "Search the repo, edit files, run tests, open PRs. Tell me the goal and I'll handle the mechanical parts."
        },
        {
          headline: 'Ready when you are.',
          body: "Type a task, question, or snippet. I remember the session, cite my sources, and stop to ask when I'm unsure."
        }
      ],
      fallback: [
        {
          headline: 'What are we moving today?',
          body: "Send a bug, branch, plan, or rough idea. I'll inspect the repo and turn it into the next concrete step."
        },
        {
          headline: "What's on your mind?",
          body: "Bring the code, question, or stuck part. I'll read the room before making changes."
        },
        {
          headline: 'What should Zast look at?',
          body: "Send the task, failing path, or half-formed plan. I'll help turn it into action."
        },
        {
          headline: 'Where should we start?',
          body: "Bring the problem, goal, or file. I'll inspect first and keep the next step concrete."
        },
        {
          headline: 'What needs attention?',
          body: "Send the context you have. I'll help sort it into a plan or a fix."
        }
      ]
    }
  },

  modelPicker: {
    title: 'Switch model',
    current: 'current:',
    unknown: '(unknown)',
    search: 'Filter providers and models...',
    noModels: 'No models found.',
    persistGlobalSession: 'Persist globally (otherwise this session only)',
    persistGlobal: 'Persist globally',
    loadFailed: 'Could not load models',
    pro: 'Pro',
    proNeedsSubscription: 'Pro models need a paid Nous subscription.',
    free: 'Free',
    freeTier: 'Free tier',
    priceTitle: 'Input / Output price per million tokens'
  },

  modelVisibility: {
    title: 'Models',
    search: 'Search models'
  },

  shell: {
    windowControls: 'Window controls',
    paneControls: 'Pane controls',
    appControls: 'App controls',
    modelMenu: {
      search: 'Search models',
      noModels: 'No models found',
      editModels: 'Edit Models…',
      fast: 'Fast',
      medium: 'Med'
    },
    modelOptions: {
      noOptions: 'No options for this model',
      options: 'Options',
      thinking: 'Thinking',
      fast: 'Fast',
      effort: 'Effort',
      minimal: 'Minimal',
      low: 'Low',
      medium: 'Medium',
      high: 'High',
      max: 'Max',
      updateFailed: 'Model option update failed',
      fastFailed: 'Fast mode update failed'
    },
    gatewayMenu: {
      gateway: 'Gateway',
      connected: 'Connected',
      connecting: 'Connecting',
      offline: 'Offline',
      inferenceReady: 'Inference ready',
      inferenceNotReady: 'Inference not ready',
      checkingInference: 'Checking inference',
      disconnected: 'Disconnected',
      openSystem: 'Open system panel',
      connection: label => `Connection: ${label}`,
      recentActivity: 'Recent activity',
      viewAllLogs: 'View all logs →',
      messagingPlatforms: 'Messaging platforms'
    },
    statusbar: {
      unknown: 'unknown',
      restart: 'restart',
      update: 'update',
      updateInProgress: 'Update in progress',
      commitsBehind: (count, branch) => `${count} commit${count === 1 ? '' : 's'} behind ${branch}`,
      desktopVersion: version => `Zast Desktop v${version}`,
      backendVersion: version => `Backend v${version}`,
      clientLabel: version => `client v${version}`,
      backendLabel: version => `backend v${version}`,
      commit: sha => `commit ${sha}`,
      branch: branch => `branch ${branch}`,
      closeCommandCenter: 'Close Command Center',
      openCommandCenter: 'Open Command Center',
      gateway: 'Gateway',
      gatewayReady: 'ready',
      gatewayNeedsSetup: 'needs setup',
      gatewayChecking: 'checking',
      gatewayConnecting: 'connecting',
      gatewayOffline: 'offline',
      gatewayTitle: 'Zast inference gateway status',
      agents: 'Agents',
      closeAgents: 'Close agents',
      openAgents: 'Open agents',
      subagents: count => `${count} subagent${count === 1 ? '' : 's'}`,
      failed: count => `${count} failed`,
      running: count => `${count} running`,
      cron: 'Cron',
      openCron: 'Open cron jobs',
      turnRunning: 'Running',
      currentTurnElapsed: 'Current turn elapsed',
      contextUsage: 'Context usage',
      session: 'Session',
      runtimeSessionElapsed: 'Runtime session elapsed',
      yoloOn: 'YOLO on — auto-approving dangerous commands. Click to turn off. Shift+click toggles it globally.',
      yoloOff: 'YOLO off — click to auto-approve dangerous commands. Shift+click toggles it globally.',
      switchModel: 'Switch model',
      openModelPicker: 'Open model picker',
      modelTitle: (provider, model) => `Model · ${provider}: ${model}`,
      providerModelTitle: (provider, model) => `${provider} · ${model}`
    }
  },

  rightSidebar: {
    aria: 'Right sidebar',
    panelsAria: 'Right sidebar panels',
    files: 'File system',
    terminal: 'Terminal',
    noFolderSelected: 'No folder selected',
    changeCwdTitle: 'Change working directory',
    folderTip: cwd => `${cwd} — click to change folder`,
    openFolder: 'Open folder',
    refreshTree: 'Refresh tree',
    collapseAll: 'Collapse all folders',
    previewUnavailable: 'Preview unavailable',
    couldNotPreview: path => `Could not preview ${path}`,
    noProjectTitle: 'No project',
    noProjectBody: 'Set a working directory from the status bar to browse files.',
    unreadableTitle: 'Unreadable',
    unreadableBody: error => `Could not read this folder (${error}).`,
    emptyTitle: 'Empty',
    emptyBody: 'This folder is empty.',
    treeErrorTitle: 'Tree error',
    treeErrorBody: 'The file tree hit an error rendering this folder.',
    tryAgain: 'Try again',
    loadingTree: 'Loading file tree',
    loadingFiles: 'Loading files',
    terminalFocus: 'Focus terminal view',
    terminalSplit: 'Return to split view',
    addToChat: 'Add to chat'
  },

  preview: {
    tab: 'Preview',
    closeTab: label => `Close ${label}`,
    closePane: 'Close preview pane',
    loading: 'Loading preview',
    unavailable: 'Preview unavailable',
    opening: 'Opening...',
    hide: 'Hide',
    openPreview: 'Open preview',
    sourceLineTitle: 'Click to select · shift-click to extend · drag to composer',
    source: 'SOURCE',
    renderedPreview: 'PREVIEW',
    unknownSize: 'unknown size',
    binaryTitle: 'This looks like a binary file',
    binaryBody: label => `Previewing ${label} may show unreadable text.`,
    largeTitle: 'This file is large',
    largeBody: (label, size) => `${label} is ${size}. Zast will only show the first 512 KB.`,
    previewAnyway: 'Preview anyway',
    truncated: 'Showing first 512 KB.',
    noInlineTitle: 'No inline preview',
    noInlineBody: mimeType => `${mimeType || 'This file type'} can still be attached as context.`,
    console: {
      deselect: 'Deselect entry',
      select: 'Select entry',
      copyFailed: 'Could not copy console output',
      copyEntry: 'Copy this entry',
      sendEntry: 'Send this entry to chat',
      messages: count => `${count} console messages`,
      resize: 'Resize preview console',
      title: 'Preview Console',
      selected: count => `${count} selected`,
      sendToChat: 'Send to chat',
      copySelected: 'Copy selected to clipboard',
      copyAll: 'Copy all to clipboard',
      copy: 'Copy',
      clear: 'Clear',
      empty: 'No console messages yet.',
      promptHeader: 'Preview console:',
      sentTitle: 'Sent to chat',
      sentMessage: count => `${count} log entr${count === 1 ? 'y' : 'ies'} added to composer`
    },
    web: {
      appFailedToBoot: 'Preview app failed to boot',
      serverNotFound: 'Server not found',
      failedToLoad: 'Preview failed to load',
      tryAgain: 'Try again',
      unknownError: 'unknown error',
      hideConsole: 'Hide preview console',
      showConsole: 'Show preview console',
      hideDevTools: 'Hide preview DevTools',
      openDevTools: 'Open preview DevTools',
      workspaceReloading: 'Workspace changed, reloading preview',
      fileChanged: url => `File changed, reloading preview: ${url}`,
      filesChanged: (count, url) => `${count} file changes, reloading preview: ${url}`,
      watchFailed: message => `Could not watch preview file: ${message}`,
      moduleMimeDescription:
        'Module scripts are being served with the wrong MIME type. This usually means a static file server is serving a Vite/React app instead of the project dev server.',
      loadFailedConsole: (code, message) => `Load failed${code ? ` (${code})` : ''}: ${message}`,
      unreachableDescription: 'The preview page could not be reached.',
      openTarget: url => `Open ${url}`,
      fallbackTitle: 'Preview'
    }
  },

  assistant: {
    thread: {
      loadingSession: 'Loading session',
      loadingResponse: 'Zast is loading a response',
      thinking: 'Thinking',
      today: time => `Today, ${time}`,
      yesterday: time => `Yesterday, ${time}`,
      copy: 'Copy',
      refresh: 'Refresh',
      moreActions: 'More actions',
      branchNewChat: 'Branch in new chat',
      readAloudFailed: 'Read aloud failed',
      preparingAudio: 'Preparing audio...',
      stopReading: 'Stop reading',
      readAloud: 'Read aloud',
      editMessage: 'Edit message',
      stop: 'Stop',
      editableCheckpoint: 'Editable checkpoint',
      restorePrevious: 'Restore previous checkpoint',
      restoreCheckpoint: 'Restore checkpoint',
      restoreNext: 'Restore next checkpoint',
      goForward: 'Go forward',
      sendEdited: 'Send edited message'
    },
    approval: {
      gatewayDisconnected: 'Zast gateway is not connected',
      sendFailed: 'Could not send approval response',
      run: 'Run',
      moreOptions: 'More approval options',
      allowSession: 'Allow this session',
      alwaysAllowMenu: 'Always allow…',
      reject: 'Reject',
      alwaysTitle: 'Always allow this command?',
      alwaysDescription: pattern =>
        `This adds the “${pattern}” pattern to your permanent allowlist (~/.zast/config.yaml). Zast won’t ask again for commands like this — in this session or any future one.`,
      alwaysAllow: 'Always allow'
    },
    clarify: {
      notReady: 'Clarify request is not ready yet',
      gatewayDisconnected: 'Zast gateway is not connected',
      sendFailed: 'Could not send clarify response',
      loadingQuestion: 'Loading question…',
      other: 'Other (type your answer)',
      placeholder: 'Type your answer…',
      shortcut: '⌘/Ctrl + Enter to send',
      back: 'Back',
      skip: 'Skip',
      send: 'Send'
    },
    tool: {
      code: 'Code',
      copyCode: 'Copy code',
      renderingImage: 'Rendering image',
      copyOutput: 'Copy output',
      copyCommand: 'Copy command',
      copyContent: 'Copy content',
      copyUrl: 'Copy URL',
      copyResults: 'Copy results',
      copyQuery: 'Copy query',
      copyFile: 'Copy file',
      copyPath: 'Copy path',
      outputAlt: 'Tool output',
      rawResponse: 'Raw response',
      copyActivity: 'Copy activity',
      recoveredOne: 'Recovered after 1 failed step',
      recoveredMany: count => `Recovered after ${count} failed steps`,
      failedOne: '1 step failed',
      failedMany: count => `${count} steps failed`,
      statusRunning: 'Running',
      statusError: 'Error',
      statusRecovered: 'Recovered',
      statusDone: 'Done'
    }
  },

  prompts: {
    gatewayDisconnected: 'Zast gateway is not connected',
    sudoSendFailed: 'Could not send sudo password',
    secretSendFailed: 'Could not send secret',
    sudoTitle: 'Administrator password',
    sudoDesc: 'Zast needs your sudo password to run a privileged command. It is sent only to your local agent.',
    sudoPlaceholder: 'sudo password',
    secretTitle: 'Secret required',
    secretDesc: 'Zast needs a credential to continue.',
    secretPlaceholder: 'secret value'
  },

  desktop: {
    audioReadFailed: 'Could not read recorded audio',
    sessionUnavailable: 'Session unavailable',
    createSessionFailed: 'Could not create a new session',
    promptFailed: 'Prompt failed',
    providerCredentialRequired: 'Add a provider credential before sending your first message.',
    emptySlashCommand: 'empty slash command',
    desktopCommands: 'Desktop commands',
    skillCommandsAvailable: count => `${count} skill commands available.`,
    warningLine: message => `warning: ${message}`,
    yoloArmed: 'YOLO armed for this chat',
    yoloOff: 'YOLO off',
    yoloSystem: active => `YOLO ${active ? 'on' : 'off'} for this session`,
    yoloTitle: 'YOLO',
    yoloToggleFailed: 'Could not toggle YOLO',
    profileStatus: current =>
      `Profile: ${current}. Use /profile <name> or the "New session" picker to start a chat in another profile.`,
    unknownProfile: 'Unknown profile',
    noProfileNamed: (target, available) => `No profile named "${target}". Available: ${available}`,
    newChatsProfile: name => `New chats will use profile ${name}.`,
    setProfileFailed: 'Failed to set profile',
    sttDisabled: 'Speech-to-text is disabled in settings.',
    stopFailed: 'Stop failed',
    regenerateFailed: 'Regenerate failed',
    editFailed: 'Edit failed',
    resumeFailed: 'Resume failed',
    nothingToBranch: 'Nothing to branch',
    branchNeedsChat: 'Start or resume a chat before branching.',
    sessionBusy: 'Session busy',
    branchStopCurrent: 'Stop the current turn before branching this chat.',
    branchNoText: 'This message has no text to branch from.',
    branchTitle: 'Branch',
    branchFailed: 'Branch failed',
    deleteFailed: 'Delete failed',
    archived: 'Archived',
    archiveFailed: 'Archive failed',
    cwdChangeFailed: 'Working directory change failed',
    cwdStagedTitle: 'Working directory staged',
    cwdStagedMessage: 'Restart the desktop backend to apply cwd changes to this active session.',
    modelSwitchFailed: 'Model switch failed',
    sessionExported: 'Session exported',
    sessionExportFailed: 'Could not export session',
    imageSaved: 'Image saved',
    downloadStarted: 'Download started',
    restartToUseSaveImage: 'Restart Zast Desktop to use Save Image.',
    restartToSaveImages: 'Restart Zast Desktop to save images',
    imageDownloadFailed: 'Image download failed',
    openImage: 'Open image',
    downloadImage: 'Download image',
    savingImage: 'Saving image',
    imagePreviewFailed: 'Image preview failed',
    imageAttach: 'Image attach',
    imageWriteFailed: 'Failed to write image to disk.',
    imageAttachFailed: 'Image attach failed',
    attachImages: 'Attach images',
    clipboard: 'Clipboard',
    noClipboardImage: 'No image found in clipboard',
    clipboardPasteFailed: 'Clipboard paste failed',
    dropFiles: 'Drop files'
  },

  errors: {
    genericFailure: 'Something went wrong',
    boundaryTitle: 'Something broke in the interface',
    boundaryDesc: 'The view hit an unexpected error. Your chats and settings are safe.',
    reloadWindow: 'Reload window',
    openLogs: 'Open logs'
  },

  recordingToolbar: {
    statusReady: 'Ready to record',
    statusRecording: 'Recording',
    statusPaused: 'Paused',
    statusProcessing: 'Finalizing…',
    statusUploadFailed: 'Upload failed',
    pause: 'Pause',
    resume: 'Resume',
    stop: 'Stop',
    saving: 'Saving…',
    uploading: 'Uploading…',
    uploadingEta: seconds => `~${seconds}s left`,
    timeoutNotice: seconds => `Over time limit — will stop in ${seconds}s`
  },

  ui: {
    search: {
      clear: 'Clear search'
    },
    pagination: {
      label: 'pagination',
      previous: 'Prev',
      previousAria: 'Go to previous page',
      next: 'Next',
      nextAria: 'Go to next page'
    },
    sidebar: {
      title: 'Sidebar',
      description: 'Displays the mobile sidebar.',
      toggle: 'Toggle Sidebar'
    }
  }
}
