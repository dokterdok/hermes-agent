import { type PluginLocaleBundles, type PluginTranslate, usePluginI18n } from '@hermes/plugin-sdk'
import { useMemo } from 'react'

import { CANONICAL_GROUP_LOCALES, type CanonicalGroupMessages } from './canonical-group-locales'
import { ja, zh, zhHant } from './i18n-east-asian'
import { getPluginCtx } from './shared'

export type BotsMessages = {
  canonical: { [K in keyof CanonicalGroupMessages]: string }
  /** Left rail: the bot + group-chat roster. */
  editor: {
    fullConfigHint: string
    liveCapabilities: string
    editSoul: string
    remoteCapabilitiesHint: string
    skillsEnabled: (enabled: number, total: number) => string
    toolsetsEnabled: (enabled: number, total: number) => string
    mcpServers: string
    providerCustom: string
    modelCustom: string
    backToDropdowns: string
    inheritLaunch: string
    enterManually: string
    gatewayDefault: string
    modelNameExample: string
    modelSwitchFailed: string
    newDescription: string
    name: string
    title: string
    description: string
    createOn: string
    general: string
    capabilities: string
    skills: string
    tools: string
    cloneFrom: string
    freshProfile: string
    inheritedModel: string
    soul: string
    shareKeys: string
    shareKeysOn: (target: string) => string
    shareKeysHint: string
    needsModel: string
    configureModel: string
    createEmpty: string
    nameTakenHint: string
    nameFirstHint: string
    newerDesktop: string
    newerGateway: string
    emptySkillsHint: string
    defaultToolsHint: string
    catalog: string
    catalogInstalled: string
    mcpHint: string
    creating: string
    createBot: string
    auto: string
    autoHint: string
    unlock: string
    lockFace: string
    lockedHint: string
    unlockedHint: string
    noImageModel: string
    checkingImage: string
    chooseImage: string
    editDescription: (name: string, profile: string) => string
    nameTaken: (name: string) => string
    nameTakenOn: (name: string, target: string) => string
    currentConnection: (name: string) => string
    remoteHint: (target: string) => string
    cloneFromOn: (target: string) => string
    catalogHint: (source: string) => string
    sectionsFailed: (sections: string) => string
    updated: (name: string) => string
    created: (name: string) => string
    createdOn: (name: string, target: string) => string
  }
  roster: {
    search: string
    searchPlaceholder: string
    newBotOrGroup: string
    groupChats: string
    emptyTitle: string
    emptyDesc: string
    noMatchQuery: (query: string) => string
    noMatchQueryOn: (query: string, gateway: string) => string
    noMatchFiltersOn: (gateway: string) => string
    noMatchFilters: string
    clearFilters: string
    allHidden: string
    allHiddenDesc: string
    showHidden: string
    noHiddenMatch: string
    hiddenFromRoster: string
    pinned: string
    needsAttention: string
    needsInput: string
    /** The kind filter's three options, in menu order. */
    botsAndGroups: string
    botsOnly: string
    groupsOnly: string
    /** The activity filter's four options, in menu order. */
    anyActivity: string
    activeNow: string
    recentlyActive: string
    older: string
    /** How a row's owning gateway is doing — see `botSourceStatus`. */
    gatewayRemoved: string
    onDemand: string
    ready: string
    statusUnknown: string
    unavailable: string
    retryNow: string
    rosterUnavailable: (reason: string) => string
    waitingForGateway: string
  }
  /** User-made roster sections (folders the user files bots into). */
  sections: {
    newSection: string
    newTitle: string
    renameTitle: string
    nameLabel: string
    namePlaceholder: string
    create: string
    rename: string
    moveUp: string
    moveDown: string
    unassigned: string
    options: (name: string) => string
    headingTip: string
    emptyHint: string
    moveTo: string
    newSectionEllipsis: string
    removeFromSection: string
    deleted: (name: string, count: number) => string
    undo: string
  }
  /** Creating, editing and removing a bot. */
  bot: {
    newTitle: string
    editTitle: string
    editMenu: string
    helpPromptPlaceholder: string
    descriptionHint: string
    newChatWith: string
    /** Re-opens the forever-chat on purpose. A plain row click only returns to
     *  the tabs already open, so a closed Bot Chat needs an explicit ask. */
    openBotChat: string
    /** Screen-reader label for the row spinner while a cold bot chat opens. */
    openingChat: string
    /** Row context menu: pin/hide toggles, their toasts, and the groups entry. */
    pinToTop: string
    unpin: string
    pinnedToast: (name: string) => string
    unpinnedToast: (name: string) => string
    hide: string
    unhide: string
    hiddenToast: (name: string) => string
    unhiddenToast: (name: string) => string
    groupsMenu: (groups: string) => string
    manageGroups: string
    metadataLoadFailed: string
    loadFailed: string
    groupsLoadFailed: string
    thisDevice: string
    /** Roster badge tooltips per attention class; `attentionFallback` when the class is unknown. */
    attentionFallback: string
    attentionProviderAuth: string
    attentionQuota: string
    attentionMissingConfig: string
    attentionBlocked: string
    duplicate: string
    duplicateFailed: string
    deleteTitle: string
    removeFromAllGroups: string
    removeFromOtherGroups: string
    createFirstHint: string
    createFailed: string
    advanced: string
    advancedHint: string
    advancedFailed: string
    openAnotherChatUnsupported: string
    remoteConnectionsUnsupported: string
    /** Bot-open failure toasts (canonical-chat.ts notifyBotOpenFailure). The
     *  raw RPC/connection error travels in the toast `detail`, never here. */
    openNeedsUpdateTitle: string
    openNeedsUpdateMessage: (connectionLabel: string) => string
    openUnreachableTitle: string
    openUnreachableMessage: string
    openChatFailedTitle: (botName: string) => string
    openChatFailedMessage: string
    openGateways: string
    /** Stands under the bot's name in a chat it has not spoken in yet. */
    chatEmpty: string
    /** First line of a brand-new bot's forever-chat — see `kickoffText`. */
    kickoff: string
  }
  /** Avatar picker: shapes, blobs, pets, uploads, generation. */
  avatar: {
    classicShapes: string
    blobFromName: string
    unlockFollowsName: string
    randomize: string
    /** The picker's four tabs, in order. */
    tabBot: string
    tabGenerate: string
    upload: string
    tabPet: string
    removeImage: string
    removeBackToShape: string
    describePlaceholder: string
    describeHint: string
    matchTheName: string
    pickPet: string
    petLoadFailed: string
    imageTooLarge: string
    generationFailed: string
    savedLocally: string
    savedLocallyDescriptionFailed: string
    generate: string
    generating: string
  }
  /** Group chats: the room, its composer, threads and activity feed. */
  group: {
    newTitle: string
    newDesc: string
    noBots: string
    manageDesc: string
    manageTitle: string
    settingsTitle: string
    settingsDesc: string
    nameLabel: string
    holdDetection: string
    holdDetectionHint: string
    compressHistory: string
    compressHistoryHint: (member: string) => string
    compressing: (member: string) => string
    compressDone: (member: string, compressed: number, detail: string) => string
    compressNothing: (member: string) => string
    compressFailed: (member: string, error: string) => string
    searchToAdd: string
    searchToAddPlaceholder: string
    removeFromSelection: string
    disbandTitle: string
    deleteTitle: string
    deleteAction: string
    composerPlaceholder: string
    slashCommandsUnsupported: string
    attachHint: string
    downloadAttachment: string
    attachmentDownloadFailed: string
    newThread: string
    reply: string
    replyInThread: string
    replyInThreadPlaceholder: string
    openThread: string
    collapseThread: string
    collapseThreadLabel: string
    activity: string
    noActivityYet: string
    showActivity: string
    hideActivity: string
    stop: string
    stopHint: string
    allHeldStatus: (count: number) => string
    heldMembersStatus: (members: string) => string
    holdReleaseHint: string
    needsYourInput: string
    noMembersToSend: (group: string) => string
    pictureGenerationFailed: string
    createAction: (count: number) => string
    created: (name: string, count: number) => string
    detailsSyncPending: string
    createFailed: string
    creating: string
    pickAtLeastTwo: string
    thisHost: string
    hostedFallbackToDesktop: (host: string) => string
    hostedAttachmentMemberUnavailable: (members: string) => string
    hostedSending: string
    hostedWorking: string
    hostedQueued: (host: string) => string
    hostedQueuedHint: (host: string) => string
    hostedNeedsAttention: string
    hostedSendFailed: (host: string) => string
    hostedStopping: string
    hostedStopped: string
    hostedStopQueued: (host: string) => string
    hostedStopQueuedHint: (host: string) => string
    hostedUnavailable: (host: string) => string
    hostedReconnectToStop: (host: string) => string
    hostedDeleted: string
    hostedDeleteLocally: string
    hostedMembersFixed: string
    hostedRenameQueued: (host: string) => string
    hostedRenameFailed: (host: string) => string
    hostRouteMissing: string
    hostUpdateNeeded: (host: string) => string
    hostReauthNeeded: (host: string) => string
    checkAgain: string
    hostReconnectToContinue: (host: string) => string
    hostedReconnectToDelete: (host: string) => string
    hostedSyncing: string
    memberCorrectDevice: (member: string) => string
    continuityOnTitle: string
    continuityOnDesc: string
    continuityDesktopTitle: string
    continuityDesktopDesc: string
    continuityReadOnlyTitle: string
    continuityReadOnlyDesc: string
    retryTitle: string
    retryDesc: string
    retryAction: string
    reconnectAction: string
    reconnectingAction: string
    messagingReconnecting: string
    reconnectFailed: string
    botsNeedOneHost: string
    aBot: string
    memberUnavailable: (member: string) => string
    memberNeedsAttention: (member: string) => string
    memberReconnectToContinue: (member: string) => string
    memberCouldNotRespond: (member: string) => string
    memberRetryWhenOnline: (member: string) => string
    desktopStorageUnavailable: string
    hostedQueueRepaired: (count: number) => string
    hostedApprovalFailed: string
    hostedApprovalRetry: string
    hostRejectedCommand: string
    nameTaken: (name: string) => string
    memberCount: (count: number) => string
    /** The reader's own lines in a room: the transcript speaker and the roster preview. */
    you: string
    /** How many of a room's members are reachable right now. */
    availableCount: (available: number, total: number) => string
    settingsHint: (group: string) => string
    settingsLabel: (group: string) => string
    disbandHint: (group: string) => string
    disbandLabel: (group: string) => string
    disbandAction: string
    disbanding: string
    disbandDone: string
    disbanded: (group: string) => string
    /** Wraps the bolded group name, so the name can lead the sentence in
     *  languages that put it there — see core's cron.deleteDesc* pair. */
    disbandDescPrefix: string
    disbandDescSuffix: (count: number) => string
    stopped: (group: string) => string
    removeAttachment: string
    threadFallback: string
    replyCount: (replies: number) => string
    dropToThread: string
    dropToRoom: string
    waitingForAnswer: string
    memberThinking: (name: string) => string
    roomWorking: string
    messageRoom: (group: string) => string
    newThreadPlaceholder: (group: string) => string
    everyoneMeta: string
    commandApproval: string
    answerFailed: (handle: string, error: string) => string
    wantsToRunCommand: (handle: string) => string
    asks: (handle: string) => string
    answerTo: (member: string) => string
  }
  /** Skills hub + MCP setup surfaces embedded in the bot editor. */
  tools: {
    installHint: (name: string) => string
    installed: (name: string) => string
    installFailed: (name: string) => string
    searchHint: string
    resizeHint: string
    addServerFailed: string
    noTarget: string
    setKeyFailed: (name: string) => string
    configured: (name: string) => string
    authenticated: (name: string) => string
    testFailed: string
    completeSignIn: string
    needsSetup: (name: string) => string
    setUpDone: string
    saveTest: string
    authorizing: string
    working: string
    setupFailed: string
    signIn: string
    setUp: string
    skillsHub: string
    filterSkills: string
    searchHub: string
    noMcpServers: string
  }

  /** Bot Screen: the bot's headless desktop on the gateway host, live in a pane. */
  screen: {
    title: string
    menu: string
    unsupportedTitle: string
    unsupportedBody: string
    notInstalledTitle: string
    notInstalledBody: string
    installHint: string
    install: string
    installing: string
    installCancelled: string
    installFailed: string
    noPackageManager: string
    portalTitle: string
    portalOpen: string
    heroStopped: string
    heroNotInstalled: string
    heroConnecting: string
    heroStale: string
    heroSuppressed: string
    heroOpenLive: string
    heroInstall: string
    heroStart: string
    portalWatching: string
    portalYouControl: string
    portalOtherControls: string
    portalStopped: string
    portalNotInstalled: string
    portalUnsupported: string
    portalUnavailable: string
    /** Managed runtimes (Hermes Cloud): updates are the platform's job, not the user's. */
    portalUnavailableManaged: string
    unavailableTitle: string
    autoOpenMenu: string
    autoOpenOnToast: (name: string) => string
    autoOpenOffToast: (name: string) => string
    stoppedTitle: string
    stoppedBody: string
    start: string
    attaching: string
    streamLost: string
    reconnect: string
    takeOver: string
    handBack: string
    handBackForce: string
    handBackForceHint: string
    openNeedsUpdate: string
    youControl: string
    otherControls: string
    agentControls: string
    controlTaken: string
  }

  /** Bot-scoped scheduled jobs. Generic scheduling chrome (weekday names,
   *  Daily/Hourly, the job verbs) resolves against core's `cron` section. */
  cron: {
    untitled: string
    nameNul: string
    instructionNul: string
    minutesFromNow: string
    hoursFromNow: string
    daysFromNow: string
    stopAfter: string
    runsHint: string
    detailDescription: string
    status: string
    active: string
    paused: string
    schedule: string
    rawSchedule: string
    repeat: string
    nextRun: string
    overdueSince: string
    lastRun: string
    lastResult: string
    workdir: string
    succeeded: string
    failed: string
    deliveryFailed: string
    blockedConfig: string
    legacyUnsafe: string
    filterHint: string
    needsRosterFirst: string
    staleNotice: string
    readFailure: string
    createDesc: (bot: string) => string
    instruction: string
    whenToRun: string
    dayOfMonth: string
    sendResultsTo: string
    runHistoryOnly: string
    botChatTarget: (bot: string) => string
    continuity: string
    onceIn: (when: string) => string
    everyNDays: (days: number) => string
    everyNHours: (hours: number) => string
    everyNMinutes: (minutes: number) => string
    /** The frequency picker's eight options, in menu order. */
    freqOnce: string
    freqHourly: string
    freqDaily: string
    freqWeekdays: string
    freqWeekly: string
    freqMonthly: string
    freqInterval: string
    freqAdvanced: string
    unitMinutes: string
    unitHours: string
    unitDays: string
    /** One-line plain-language read-back of the picker's current state. */
    runsOnce: (count: number, unit: string) => string
    runsHourly: string
    runsDaily: (time: string) => string
    runsWeekdays: (time: string) => string
    runsWeekly: (day: string, time: string) => string
    runsMonthly: (day: string, time: string) => string
    runsInterval: (count: number, unit: string) => string
    runsRaw: string
    timesTotal: (count: number) => string
  }
}

const en: BotsMessages = {
  canonical: CANONICAL_GROUP_LOCALES.en,
  editor: {
    fullConfigHint: 'Full configuration needs a newer gateway (restart it after updating Hermes).',
    liveCapabilities: 'Capabilities (applies immediately — skills, tools, MCP)',
    editSoul: 'SOUL.md (persona + agent-messaging protocol)',
    remoteCapabilitiesHint:
      'Remote capabilities require a newer desktop. Model and SOUL changes remain staged until you save.',
    skillsEnabled: (enabled, total) => `Skills (${enabled}/${total} enabled)`,
    toolsetsEnabled: (enabled, total) => `Toolsets (${enabled}/${total} enabled — unchecking all restores the default)`,
    mcpServers: 'MCP servers',
    providerCustom: 'Provider (Custom)',
    modelCustom: 'Model (Custom)',
    backToDropdowns: '← Back to dropdowns',
    inheritLaunch: 'Inherit (launch profile)',
    enterManually: '✏️ Enter manually…',
    gatewayDefault: 'gateway default',
    modelNameExample: 'e.g. model name',
    modelSwitchFailed: 'Model switch failed',
    newDescription: 'A named teammate with its own memory, skills, and chat. It can message your other agents.',
    name: 'Name',
    title: 'Title',
    description: 'Description',
    createOn: 'Create on',
    general: 'General',
    capabilities: 'Capabilities',
    skills: 'Skills',
    tools: 'Tools',
    cloneFrom: 'Clone from profile',
    freshProfile: 'Fresh profile (bundled skills)',
    inheritedModel: 'inherited from launch profile',
    soul: 'SOUL.md (optional — replaces the generated persona)',
    shareKeys: 'Share keys & accounts with the main profile',
    shareKeysOn: target => `Share keys & accounts with the default profile on ${target}`,
    shareKeysHint:
      'Subscriptions, OAuth logins, and API keys stay shared (not copied), so token refreshes never invalidate each other. Uncheck for an isolated snapshot copy.',
    needsModel:
      'No model provider is ready for it yet, so it skipped its introduction. Pick a provider and model under Advanced.',
    configureModel: 'Configure model',
    createEmpty: 'Create empty (skip bundled skills)',
    nameTakenHint: 'That name is taken — pick another before configuring capabilities.',
    nameFirstHint: 'Name the bot first — a draft profile is created when you open this tab (discarded if you cancel).',
    newerDesktop: 'Skills need a newer Hermes Desktop.',
    newerGateway: 'Capability catalog needs a newer gateway (restart it after updating Hermes).',
    emptySkillsHint: '“Create empty” is checked — no bundled skills will be installed.',
    defaultToolsHint: 'Leaving all (or none) checked keeps the default toolset behavior.',
    catalog: 'catalog',
    catalogInstalled: 'catalog · installed',
    mcpHint:
      'Configured servers copy from the main profile; catalog entries are the bundled MCP menu. Entries needing API keys route through setup first (credentials follow the shared keys setting).',
    creating: 'Creating…',
    createBot: 'Create Bot',
    auto: 'Auto',
    autoHint: 'Auto — the name decides',
    unlock: 'Unlock',
    lockFace: 'Lock face',
    lockedHint: 'Face locked — renaming won’t change it.',
    unlockedHint: 'Face follows the name.',
    noImageModel:
      'No image model available. If you just enabled one (or updated Hermes), restart the gateway: Ctrl+K → "Restart gateway".',
    checkingImage: 'Checking image backend…',
    chooseImage: 'Choose an image…',
    editDescription: (name, profile) => `Appearance and role for ${name} (${profile}).`,
    nameTaken: name => `An agent named "${name}" already exists.`,
    nameTakenOn: (name, target) => `An agent named "${name}" already exists on ${target}.`,
    currentConnection: name => `${name} (current)`,
    remoteHint: target =>
      `The agent is created on ${target} and appears in the roster as a Connections bot. Chat routes to that machine.`,
    cloneFromOn: target => `Clone from profile (on ${target})`,
    catalogHint: source => `Catalog from ${source} — unchecked skills are disabled after creation.`,
    sectionsFailed: sections => `Some sections failed: ${sections}`,
    updated: name => `${name} updated`,
    created: name => `Bot "${name}" created`,
    createdOn: (name, target) => `Bot "${name}" created on ${target}`
  },
  roster: {
    search: 'Search bots and group chats',
    searchPlaceholder: 'Search bots and group chats…',
    newBotOrGroup: 'New bot or group chat',
    groupChats: 'Group chats',
    emptyTitle: 'No bots yet',
    emptyDesc: 'Create your first bot.',
    noMatchQuery: query => `No bots or group chats match “${query}”`,
    noMatchQueryOn: (query, gateway) => `No bots or group chats match “${query}” on ${gateway}`,
    noMatchFiltersOn: gateway => `No bots or group chats match these filters on ${gateway}`,
    noMatchFilters: 'No bots or group chats match these filters.',
    clearFilters: 'Clear filters',
    allHidden: 'All bots are hidden',
    allHiddenDesc: 'They keep working and retain their history.',
    showHidden: 'Show hidden bots',
    noHiddenMatch: 'No hidden bots match these filters.',
    hiddenFromRoster: 'Hidden from the roster',
    pinned: 'Pinned',
    needsAttention: 'needs attention',
    needsInput: 'Needs your input',
    botsAndGroups: 'Bots and group chats',
    botsOnly: 'Bots only',
    groupsOnly: 'Group chats only',
    anyActivity: 'Any activity',
    activeNow: 'Active now',
    recentlyActive: 'Recently active',
    older: 'Older',
    gatewayRemoved: 'Gateway removed',
    onDemand: 'On demand',
    ready: 'Ready',
    statusUnknown: 'Status unknown',
    unavailable: 'Unavailable',
    retryNow: 'Retry now',
    rosterUnavailable: reason =>
      `Roster unavailable: ${reason}. If your gateway predates profiles.list, update Hermes and restart the gateway.`,
    waitingForGateway:
      'Waiting for the gateway connection… (remote gateways can take a few seconds; retries automatically)'
  },
  sections: {
    newSection: 'New section',
    newTitle: 'New section',
    renameTitle: 'Rename section',
    nameLabel: 'Section name',
    namePlaceholder: 'e.g. Clients',
    create: 'Create',
    rename: 'Rename…',
    moveUp: 'Move up',
    moveDown: 'Move down',
    unassigned: 'Unassigned',
    options: name => `${name} section options`,
    headingTip: 'Drop bots here · double-click to rename',
    emptyHint: 'Drag bots here',
    moveTo: 'Move to section',
    newSectionEllipsis: 'New section…',
    removeFromSection: 'Remove from section',
    deleted: (name, count) =>
      count === 0
        ? `Deleted “${name}”`
        : `Deleted “${name}” — ${count} ${count === 1 ? 'bot' : 'bots'} moved to Unassigned`,
    undo: 'Undo'
  },
  bot: {
    newTitle: 'New bot',
    editTitle: 'Edit profile',
    editMenu: 'Edit…',
    helpPromptPlaceholder: 'What should this bot help with?',
    descriptionHint: 'Leave blank to generate from the bot’s name and description.',
    newChatWith: 'New chat with this bot',
    openBotChat: 'Open Bot Chat',
    openingChat: 'Opening chat…',
    pinToTop: 'Pin to top',
    unpin: 'Unpin',
    pinnedToast: name => `${name} pinned to top`,
    unpinnedToast: name => `${name} unpinned`,
    hide: 'Hide',
    unhide: 'Unhide',
    hiddenToast: name => `${name} hidden — use the eye button in the Bots header to see hidden bots`,
    unhiddenToast: name => `${name} is back in the roster`,
    groupsMenu: groups => `Groups: ${groups}…`,
    manageGroups: 'Manage groups…',
    metadataLoadFailed: 'Could not load bot metadata',
    loadFailed: 'Could not load bot',
    groupsLoadFailed: 'Could not load bot groups',
    thisDevice: 'This device',
    attentionFallback: 'Needs attention',
    attentionProviderAuth: 'Sign in again for this profile',
    attentionQuota: 'Quota or balance exhausted',
    attentionMissingConfig: 'Provider not configured — run hermes model',
    attentionBlocked: 'Bot is blocked — see its last message',
    duplicate: 'Duplicate',
    duplicateFailed: 'Duplicate failed',
    deleteTitle: 'Delete bot and profile?',
    removeFromAllGroups: 'Remove from all groups',
    removeFromOtherGroups: 'Leave other groups',
    createFirstHint: 'Open the Bots pane and hit “New Bot”.',
    createFailed: 'Could not create the profile yet',
    advanced: 'Advanced',
    advancedHint: 'Advanced — model, skills, toolsets, SOUL.md',
    advancedFailed: 'Advanced configuration failed',
    openAnotherChatUnsupported: 'Update Hermes Desktop to open another Bot chat.',
    remoteConnectionsUnsupported: 'Update Hermes Desktop to chat with bots on other connections.',
    openNeedsUpdateTitle: 'This bot lives on an older Hermes',
    openNeedsUpdateMessage: connectionLabel => `Update ${connectionLabel}, then try again.`,
    openUnreachableTitle: 'Hermes couldn’t reach the computer this bot runs on',
    openUnreachableMessage: 'Check it is online and try again.',
    openChatFailedTitle: botName => `Could not open ${botName}’s chat`,
    openChatFailedMessage: 'Try again.',
    openGateways: 'Open Gateways',
    chatEmpty: 'Say something to get started.',
    kickoff: 'Hey, tell me about yourself!'
  },
  avatar: {
    classicShapes: 'Classic shapes',
    blobFromName: 'Blob face — drawn from the bot’s name',
    unlockFollowsName: 'Unlock — the face follows the bot’s name again',
    randomize: 'Randomize',
    tabBot: 'Bot',
    tabGenerate: 'Generate',
    upload: 'Upload',
    tabPet: 'Pet',
    removeImage: 'Remove image — use shape',
    removeBackToShape: 'Remove — back to shape avatar',
    describePlaceholder: 'Describe your avatar…',
    describeHint: 'Leave blank to auto-generate from name/title/description + agent-messaging roster.',
    matchTheName: 'Match the name',
    pickPet: 'Pick a pet as this bot’s profile picture.',
    petLoadFailed: 'Could not load that pet — try another.',
    imageTooLarge: 'Image too large (max 15MB).',
    generationFailed: 'Avatar generation failed',
    savedLocally: 'Saved look locally; remote persistence failed',
    savedLocallyDescriptionFailed: 'Saved look locally; description update failed',
    generate: 'Generate',
    generating: 'Generating…'
  },
  group: {
    newTitle: 'New group chat',
    newDesc: 'Choose 2–6 Bots.',
    noBots: 'No bots yet. Create a bot first.',
    manageDesc: 'A Bot can join more than one Group Chat.',
    manageTitle: 'Manage groups',
    settingsTitle: 'Group settings',
    settingsDesc: 'Rename the group or set a room picture. Members and history are kept.',
    nameLabel: 'Group name',
    holdDetection: 'Detect stop directives',
    holdDetectionHint: 'Let room messages put addressed members on hold until they are mentioned again.',
    compressHistory: 'Compress history',
    compressHistoryHint: (member: string) =>
      `Compress ${member}'s hidden room history so the member stops failing with empty replies`,
    compressing: (member: string) => `Compressing ${member}'s room history…`,
    compressDone: (member: string, compressed: number, detail: string) =>
      `Compressed ${compressed} room session${compressed === 1 ? '' : 's'} for ${member}${detail ? ` — ${detail}` : ''}`,
    compressNothing: (member: string) => `Nothing to compress for ${member} — no room session yet`,
    compressFailed: (member: string, error: string) => `Could not compress ${member}'s room history: ${error}`,
    searchToAdd: 'Search bots to add',
    searchToAddPlaceholder: 'Search bots to add…',
    removeFromSelection: 'Remove from selection',
    disbandTitle: 'Disband group chat?',
    deleteTitle: 'Delete group chat?',
    deleteAction: 'Delete',
    composerPlaceholder: 'Say something — every bot in this group hears the room.',
    slashCommandsUnsupported:
      'Slash commands are not supported in group chats. Open an individual bot chat to use them.',
    attachHint: 'Attach files — every responding bot sees them',
    downloadAttachment: 'Download attachment',
    attachmentDownloadFailed: 'This attachment could not be downloaded.',
    newThread: 'New Thread',
    reply: 'Reply',
    replyInThread: 'Reply in thread',
    replyInThreadPlaceholder: 'Reply in thread…',
    openThread: 'Open this thread',
    collapseThread: 'Collapse thread',
    collapseThreadLabel: 'Collapse this thread',
    activity: 'Activity',
    noActivityYet: 'No activity in this turn yet.',
    showActivity: 'Show room activity',
    hideActivity: 'Hide room activity',
    stop: 'Stop',
    stopHint: 'Stop this run — interrupts the member on turn and holds the rest',
    allHeldStatus: count => `All ${count} bots are paused`,
    heldMembersStatus: members => `Paused: ${members}`,
    holdReleaseHint: 'Mention a paused bot or send @all resume to release them.',
    needsYourInput: 'A bot in this group chat needs your input',
    noMembersToSend: group =>
      `${group} has no members to send to — add a bot, or reopen the room if members are still loading.`,
    pictureGenerationFailed: 'Group picture generation failed',
    createAction: count => `Create Group${count ? ` (${count})` : ''}`,
    created: (name, count) => `“${name}” created with ${count} bots`,
    detailsSyncPending: 'Some Bot details haven’t synced to your other devices.',
    createFailed: 'Could not create the Group Chat. Try again.',
    creating: 'Creating…',
    pickAtLeastTwo: 'Pick at least 2 bots',
    thisHost: 'this device',
    hostedFallbackToDesktop: host => `${host} can't keep this Group Chat running yet. Keep Desktop open.`,
    hostedAttachmentMemberUnavailable: members =>
      `Files cannot reach ${members || 'every Bot'} right now. Check the affected gateway connection and try again.`,
    hostedSending: 'Sending…',
    hostedWorking: 'Working',
    hostedQueued: host => `Waiting for ${host}`,
    hostedQueuedHint: host => `Saved. It will send when ${host} is online.`,
    hostedNeedsAttention: 'Needs attention',
    hostedSendFailed: host => `Not sent. Reconnect ${host} and retry.`,
    hostedStopping: 'Stopping…',
    hostedStopped: 'Stopped',
    hostedStopQueued: host => `Stop requested. It will stop when ${host} is online.`,
    hostedStopQueuedHint: host => `It will stop when ${host} is online.`,
    hostedUnavailable: host => `${host} is offline`,
    hostedReconnectToStop: host => `Reconnect ${host} to stop this Group Chat.`,
    hostedDeleted: 'This Group Chat was deleted.',
    hostedDeleteLocally: 'Delete it here to remove its local membership and history.',
    hostedMembersFixed: 'Members cannot change while this Group Chat keeps running without Desktop.',
    hostedRenameQueued: host => `Rename saved. It will sync when ${host} is online.`,
    hostedRenameFailed: host => `Could not rename. Reconnect ${host} and retry.`,
    hostRouteMissing: 'This Group Chat connection is unavailable.',
    hostUpdateNeeded: host => `Update ${host} to keep this Group Chat running.`,
    hostReauthNeeded: host => `Sign in to ${host} again, then check this Group Chat.`,
    checkAgain: 'Check again',
    hostReconnectToContinue: host => `Reconnect ${host} to continue.`,
    hostedReconnectToDelete: host => `Reconnect ${host} to delete this Group Chat.`,
    hostedSyncing: 'Syncing recent activity…',
    memberCorrectDevice: member => `Reconnect ${member} from the device where this Bot is installed, then check again.`,
    continuityOnTitle: 'Works without Desktop',
    continuityOnDesc: 'Bots can continue while Desktop is closed.',
    continuityDesktopTitle: 'Keep Desktop open',
    continuityDesktopDesc: 'Bots pause when Desktop closes.',
    continuityReadOnlyTitle: 'Read-only history',
    continuityReadOnlyDesc: 'This gateway can show this Group Chat, but cannot keep it running.',
    retryTitle: 'Retry uncertain work?',
    retryDesc: 'The earlier attempt may have finished. Retrying could repeat actions.',
    retryAction: 'Retry',
    reconnectAction: 'Reconnect',
    reconnectingAction: 'Connecting…',
    messagingReconnecting: 'Messaging is reconnecting. Keep Desktop open until it finishes.',
    reconnectFailed: 'Could not reconnect this Bot. Check that its device is online, then try again.',
    botsNeedOneHost: 'The selected Bots cannot continue when Desktop is closed.',
    aBot: 'A bot',
    memberUnavailable: member => `${member} is unavailable.`,
    memberNeedsAttention: member => `${member} needs your attention.`,
    memberReconnectToContinue: member => `Reconnect ${member} to continue this Group Chat.`,
    memberCouldNotRespond: member => `${member} could not respond.`,
    memberRetryWhenOnline: member => `${member} will retry when online.`,
    desktopStorageUnavailable: 'Desktop could not save this action. Try again.',
    hostedQueueRepaired: count =>
      `${count} damaged pending Group Chat ${count === 1 ? 'change was' : 'changes were'} removed; the rest were kept.`,
    hostedApprovalFailed: 'This approval is no longer available. Refresh the Group Chat and try again.',
    hostedApprovalRetry: 'Could not send this approval. Check the gateway connection and try again.',
    hostRejectedCommand: 'The connected device rejected this action.',
    nameTaken: name => `A group named “${name}” already exists.`,
    memberCount: count => `${count} bots`,
    you: 'You',
    availableCount: (available, total) => `${available} of ${total} available`,
    settingsHint: group => `Group settings — rename ${group} or set a room picture`,
    settingsLabel: group => `Group settings for ${group}`,
    disbandHint: group => `Disband the ${group} group chat`,
    disbandLabel: group => `Disband ${group}`,
    disbandAction: 'Disband',
    disbanding: 'Disbanding…',
    disbandDone: 'Disbanded',
    disbanded: group => `Disbanded “${group}”`,
    disbandDescPrefix: 'This removes the ',
    disbandDescSuffix: count =>
      ` grouping from its ${count} bots and clears the shared room log. The bots themselves and their per-group sessions are kept.`,
    stopped: group => `Stopped ${group} — remaining turns are held until you resume`,
    removeAttachment: 'Remove attachment',
    threadFallback: 'Thread',
    replyCount: replies => `${replies} ${replies === 1 ? 'reply' : 'replies'}`,
    dropToThread: 'Drop to attach to this thread reply',
    dropToRoom: 'Drop to attach — every responding bot sees it',
    waitingForAnswer: 'Waiting for your answer…',
    memberThinking: name => `${name} is thinking…`,
    roomWorking: 'The room is working…',
    messageRoom: group => `Message ${group}`,
    newThreadPlaceholder: group => `New thread in ${group}… (@name to direct, @everyone for all)`,
    everyoneMeta: 'Every bot in the room',
    commandApproval: 'command approval',
    answerFailed: (handle, error) => `Could not send the answer to @${handle}: ${error}`,
    wantsToRunCommand: handle => `@${handle} wants to run a command:`,
    asks: handle => `@${handle} asks:`,
    answerTo: member => `Answer @${member}`
  },
  tools: {
    installHint: name => `Install "${name}" and add it to the list above`,
    installed: name => `Skill "${name}" installed`,
    installFailed: name => `Installing "${name}" failed`,
    searchHint: 'Searching community + well-known sources — can take ~10s…',
    resizeHint: 'Drag the corner to resize.',
    addServerFailed: 'Could not add server',
    noTarget: 'No target profile',
    setKeyFailed: key => `Failed to set ${key}`,
    configured: name => `${name} configured`,
    authenticated: name => `${name} authenticated`,
    testFailed: 'Server test failed after setup',
    completeSignIn: 'Complete sign-in in your browser...',
    needsSetup: keys => `needs setup (${keys}) — restart the gateway to enable in-app setup`,
    setUpDone: 'set up ✓',
    saveTest: 'Save & test',
    authorizing: 'Authorizing…',
    working: 'Working…',
    setupFailed: 'Setup failed',
    signIn: 'Sign in…',
    setUp: 'Set up…',
    skillsHub: 'Hermes Skills Hub',
    filterSkills: 'Filter skills…',
    searchHub: 'Search the hub (community + well-known sources)…',
    noMcpServers: 'No MCP servers configured or in the catalog.'
  },
  screen: {
    title: 'Screen',
    menu: 'Open Screen',
    unsupportedTitle: 'No bot screen on this host',
    unsupportedBody: 'Bot screens run on Linux gateway hosts. This bot uses the host\u2019s own display.',
    notInstalledTitle: 'Screen packages missing',
    notInstalledBody: 'The gateway host needs TigerVNC and the Xfce core to give this bot a screen. Run on the host:',
    installHint: 'Runs on the gateway host as the user Hermes runs as; sudo is asked for once, through Hermes.',
    install: 'Install on host',
    installing: 'Installing…',
    installCancelled: 'Install cancelled: no sudo password was provided.',
    installFailed: 'Install failed. Read the log above, or run the command on the host yourself.',
    noPackageManager: 'No supported package manager (apt, dnf, pacman) was found on the gateway host.',
    portalTitle: 'Screen',
    portalOpen: 'Open',
    heroStopped: 'Screen is off',
    heroNotInstalled: 'Not installed on this host',
    heroConnecting: 'Checking the screen…',
    heroStale: 'Last seen — screen unreachable',
    heroSuppressed: 'Hidden while someone has control',
    heroOpenLive: 'Open live',
    heroInstall: 'Install',
    heroStart: 'Start',
    portalWatching: 'Live · bot in control',
    portalYouControl: 'Live · you are in control',
    portalOtherControls: 'Live · another viewer in control',
    portalStopped: 'Stopped',
    portalNotInstalled: 'Not installed on host',
    portalUnsupported: 'Not available on this host',
    portalUnavailable: 'Update the bot\u2019s Hermes to use Screen',
    portalUnavailableManaged: 'Screen is not available on this managed Hermes release yet',
    unavailableTitle: 'Screen needs a newer Hermes',
    autoOpenMenu: 'Open Screen when the bot uses it',
    autoOpenOnToast: name => `${name}’s Screen opens when it starts using its desktop`,
    autoOpenOffToast: name => `${name}’s Screen stays closed until you open it`,
    stoppedTitle: 'Screen is off',
    stoppedBody: 'Start this bot\u2019s desktop to watch what it does and take over when it needs you.',
    start: 'Start screen',
    attaching: 'Connecting to the screen\u2026',
    streamLost: 'Screen stream ended',
    reconnect: 'Reconnect',
    takeOver: 'Take over',
    handBack: 'Hand back',
    handBackForce: 'Hand back (force)',
    handBackForceHint: 'Release a lease held by a viewer that is no longer here, e.g. after a reload.',
    openNeedsUpdate: 'Update Hermes Desktop to open bot screens.',
    youControl: 'You are in control',
    otherControls: 'Another viewer is in control',
    agentControls: 'Bot is in control',
    controlTaken: 'Another viewer took control. Watching only.'
  },
  cron: {
    untitled: 'Untitled job',
    nameNul: 'Job name cannot contain NUL (U+0000).',
    instructionNul: 'Job instruction cannot contain NUL (U+0000).',
    minutesFromNow: 'minutes from now',
    hoursFromNow: 'hours from now',
    daysFromNow: 'days from now',
    stopAfter: 'Stop after',
    runsHint: 'runs (blank = forever)',
    detailDescription: 'What this job runs, and when it runs next.',
    status: 'Status',
    active: 'Active',
    paused: 'Paused',
    schedule: 'Schedule',
    rawSchedule: 'Schedule (raw)',
    repeat: 'Repeat',
    nextRun: 'Next run',
    overdueSince: 'Overdue since',
    lastRun: 'Last run',
    lastResult: 'Last result',
    workdir: 'Working directory',
    succeeded: 'Succeeded',
    failed: 'Failed',
    deliveryFailed: 'Ran, but delivery failed',
    blockedConfig: 'Blocked by configuration (not run)',
    legacyUnsafe: 'Paused for security: delete and recreate this legacy job before running it again.',
    filterHint:
      'Scheduled jobs exist in this profile but none are tagged for this bot. Name a job "[bot:<name>] …" to show it here, or see them in Cron below.',
    needsRosterFirst: 'This bot has to appear in the roster first.',
    staleNotice: 'Could not refresh scheduled jobs. Showing the last list we had.',
    readFailure: 'The list may still be there — this was a read failure, not a delete.',
    createDesc: bot => `A recurring task ${bot} runs on a schedule. Runs land in its own chat history.`,
    instruction: 'Instruction',
    whenToRun: 'When to run',
    dayOfMonth: 'Day of month',
    sendResultsTo: 'Send results to',
    runHistoryOnly: 'Run history only',
    botChatTarget: bot => `${bot}’s chat (bot responds)`,
    continuity: 'Continuity: each run sees the previous run’s output (dedupe, continue where it left off)',
    onceIn: when => `Once (${when})`,
    everyNDays: days => `Every ${days} days`,
    everyNHours: hours => `Every ${hours}h`,
    everyNMinutes: minutes => `Every ${minutes}m`,
    freqOnce: 'Once, in…',
    freqHourly: 'Every hour',
    freqDaily: 'Every day',
    freqWeekdays: 'Weekdays',
    freqWeekly: 'Every week',
    freqMonthly: 'Every month',
    freqInterval: 'Interval',
    freqAdvanced: 'Advanced…',
    unitMinutes: 'minute(s)',
    unitHours: 'hour(s)',
    unitDays: 'day(s)',
    runsOnce: (count, unit) => `Runs once, ${count} ${unit} from now`,
    runsHourly: 'Runs at the top of every hour',
    runsDaily: time => `Runs every day at ${time}`,
    runsWeekdays: time => `Runs Monday–Friday at ${time}`,
    runsWeekly: (day, time) => `Runs every ${day} at ${time}`,
    runsMonthly: (day, time) => `Runs on day ${day} of each month at ${time}`,
    runsInterval: (count, unit) => `Runs every ${count} ${unit}`,
    runsRaw: 'Raw schedule — every Nm/Nh/Nd or 5-field cron',
    timesTotal: count => `, ${count} time(s) total`
  }
}

/** Registered via `ctx.i18n.register` at plugin load (disposer tracked). */
export const BOTS_LOCALES: PluginLocaleBundles = {
  en,
  ja,
  zh,
  'zh-hant': zhHant,
  ar: { canonical: CANONICAL_GROUP_LOCALES.ar },
  ru: { canonical: CANONICAL_GROUP_LOCALES.ru },
  fr: { canonical: CANONICAL_GROUP_LOCALES.fr },
  de: { canonical: CANONICAL_GROUP_LOCALES.de },
  es: { canonical: CANONICAL_GROUP_LOCALES.es }
}

// Bind the message SHAPE to a plugin translator: string leaves resolve now,
// function leaves forward their args through t(path, …).
type Bound<T> = {
  [K in keyof T]: T[K] extends (...args: infer A) => string
    ? (...args: A) => string
    : T[K] extends object
      ? Bound<T[K]>
      : string
}

function bind<T extends object>(t: PluginTranslate, template: T, prefix = ''): Bound<T> {
  const out = {} as Record<string, unknown>

  for (const [key, value] of Object.entries(template)) {
    const path = prefix ? `${prefix}.${key}` : key
    out[key] =
      typeof value === 'function'
        ? (...args: unknown[]) => t(path, ...args)
        : value && typeof value === 'object'
          ? bind(t, value as object, path)
          : t(path)
  }

  return out as Bound<T>
}

export type BotsText = Bound<BotsMessages>

/** The Bot Mode strings for the active locale — one hook every component reads. */
export function useBots(): BotsText {
  const t = usePluginI18n('hermes-bots')

  return useMemo(() => bind(t, en), [t])
}

/** Resolve a dotted path against the English bundle — the floor for a read
 *  that beats `ctx.i18n` into existence, so an unresolved key never ships as
 *  the literal `cron.runsHourly`. */
function english(key: string, ...args: unknown[]): string {
  const leaf = key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], en)

  return typeof leaf === 'function' ? (leaf as (...a: unknown[]) => string)(...args) : String(leaf ?? key)
}

let bound: { text: BotsText; translate: PluginTranslate } | null = null

/** `useBots` for the module-level functions a hook can't reach — the schedule
 *  summarizers and label helpers that render inside components but aren't
 *  components. Non-reactive on its own; every caller is invoked during a
 *  render that a core `useI18n()` already subscribes to, so a locale switch
 *  still repaints. Cached on translator identity: `bind` walks the whole tree,
 *  and these run per row. */
export function botsText(): BotsText {
  const translate = getPluginCtx()?.i18n?.t ?? english

  if (bound?.translate !== translate) {
    bound = { text: bind(translate, en), translate }
  }

  return bound.text
}
