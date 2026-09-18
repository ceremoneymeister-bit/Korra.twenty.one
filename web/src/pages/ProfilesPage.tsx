import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { useProfileScope } from '@/contexts/useProfileScope'
import {
  AlignLeft,
  Check,
  ChevronDown,
  Cpu,
  MoreVertical,
  Pencil,
  Package,
  Sparkles,
  Terminal,
  Trash2,
  Users,
  X
} from 'lucide-react'
import spinners from 'unicode-animations'
import { H2 } from '@nous-research/ui/ui/components/typography/h2'
import { api } from '@/lib/api'
import { buildModelChoices, choiceKey, modelKey, type ModelChoice } from '@/lib/model-choices'
import type { ActiveProfileInfo, ProfileInfo } from '@/lib/api'
import { copyTextToClipboard } from '@/lib/clipboard'
import { ownerFacingError } from '@/lib/owner-facing-error'
import ProfileLearningPanel, { type LearningSection } from '@/components/agents/ProfileLearningPanel'
import { DeleteConfirmDialog } from '@/components/DeleteConfirmDialog'
import { useToast } from '@nous-research/ui/hooks/use-toast'
import { useConfirmDelete } from '@nous-research/ui/hooks/use-confirm-delete'
import { useModalBehavior } from '@/hooks/useModalBehavior'
import { Toast } from '@nous-research/ui/ui/components/toast'
import { Card, CardContent } from '@nous-research/ui/ui/components/card'
import { Badge } from '@nous-research/ui/ui/components/badge'
import { Button } from '@nous-research/ui/ui/components/button'
import { Input } from '@nous-research/ui/ui/components/input'
import { Label } from '@nous-research/ui/ui/components/label'
import { Select, SelectOption } from '@nous-research/ui/ui/components/select'
import { useI18n } from '@/i18n'
import { usePageHeader } from '@/contexts/usePageHeader'
import { cn, themedBody } from '@/lib/utils'

function profileLabel(profile: ProfileInfo) {
  return profile.display_name?.trim() || (profile.is_default ? 'Главный агент' : profile.name)
}

/**
 * Состояние шлюза агента человеческими словами.
 *
 * До K21-088 панель различала только «есть свой процесс» и «нет». Агента,
 * которого ведёт общий шлюз, это показывало как остановленного — владелец
 * видел «выключен» у агента, отвечающего в Telegram. Три состояния:
 * работает сам, работает под общим шлюзом, не работает.
 *
 * `title` объясняет, почему у агента нет своего процесса и это нормально.
 * Старый движок поля не отдаёт — откатываемся на `gateway_running`.
 */
function gatewayBadge(profile: ProfileInfo): {
  label: string
  tone: 'success' | 'outline'
  title: string
  detail: string
} {
  const status = profile.gateway_status ?? (profile.gateway_running ? 'running' : 'stopped')
  if (status === 'served') {
    return {
      label: 'На связи — общий шлюз',
      tone: 'success',
      title:
        'Агента ведёт общий шлюз основного профиля: отдельного процесса у него нет, и это не ошибка. Остановить или перезапустить общий шлюз можно только целиком — это затронет всех агентов.',
      detail: 'Шлюз: общий шлюз основного профиля'
    }
  }
  if (status === 'running') {
    return {
      label: 'На связи',
      tone: 'success',
      title: 'У агента работает собственный шлюз.',
      detail: 'Шлюз: собственный'
    }
  }
  return {
    label: 'Не на связи',
    tone: 'outline',
    title:
      'Шлюз агента не работает: сообщения из мессенджеров и задачи по расписанию сейчас не обрабатываются.',
    detail: 'Шлюз: не работает'
  }
}

/** Braille unicode spinner (`unicode-animations`); static first frame when reduced motion is preferred. */
function ProfilesLoadingSpinner() {
  const { frames, interval } = spinners.braille
  const [frameIndex, setFrameIndex] = useState(0)

  useEffect(() => {
    if (typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      return
    }
    const id = window.setInterval(() => setFrameIndex(i => (i + 1) % frames.length), interval)
    return () => window.clearInterval(id)
  }, [frames.length, interval])

  return (
    <span aria-hidden className="inline-block select-none text-xl leading-none text-muted-foreground">
      {frames[frameIndex]}
    </span>
  )
}

/**
 * Per-card "⋯" actions menu. Holds every action for the profile (set active,
 * model, description, SOUL, copy command, rename, delete) so the card row stays
 * a single button. Mirrors the hand-rolled dropdown pattern used by ModelsPage's
 * "Use as" menu (button + absolute panel + outside-click close).
 */
function ProfileActionsMenu({
  isActive,
  isDefault,
  isEditingDesc,
  isEditingModel,
  isEditingSoul,
  labels,
  settingActive,
  onCopyCommand,
  onDelete,
  onEditDescription,
  onEditModel,
  onEditSoul,
  onManageSkills,
  onRename,
  onSetActive
}: ProfileActionsMenuProps) {
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node | null
      // Close only when the click lands outside *this* menu. Matching any
      // `[data-profile-actions]` would treat another card's menu as "inside"
      // and leave several menus open at once.
      if (target && !containerRef.current?.contains(target)) setOpen(false)
    }
    window.addEventListener('mousedown', onDown)
    return () => window.removeEventListener('mousedown', onDown)
  }, [open])

  // Run the action, then collapse the menu. Toggle editors (model/description/
  // SOUL) expand the inline section below the card once the menu closes.
  const run = (fn: () => void) => () => {
    fn()
    setOpen(false)
  }

  // `text-left` обязателен: у <button> по умолчанию `text-align: center`, и
  // длинный пункт («Управлять навыками и инструментами») переносился на две
  // строки по центру, пока соседние короткие стояли слева (QA 03.09).
  // Перенос снят целиком — меню растёт вширь, а не вниз.
  const itemClass =
    'flex w-full items-center gap-2.5 whitespace-nowrap px-3 py-2 text-left text-xs hover:bg-muted/50 disabled:opacity-40 [&>*]:shrink-0'

  return (
    <div className="relative" data-profile-actions ref={containerRef}>
      <Button
        ghost
        size="icon"
        title={labels.actions}
        aria-label={labels.actions}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(v => !v)}
      >
        <MoreVertical className="h-4 w-4" />
      </Button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-50 mt-1 w-max min-w-[200px] max-w-[calc(100vw-2rem)] bg-card shadow-lg"
        >
          {!isActive && (
            <button
              type="button"
              role="menuitem"
              className={itemClass}
              disabled={settingActive}
              onClick={run(onSetActive)}
            >
              <Check className="h-4 w-4" />
              {labels.setActive}
            </button>
          )}

          <button type="button" role="menuitem" className={itemClass} onClick={run(onEditModel)}>
            {isEditingModel ? <ChevronDown className="h-4 w-4" /> : <Cpu className="h-4 w-4" />}
            {labels.editModel}
          </button>

          <button type="button" role="menuitem" className={itemClass} onClick={run(onEditDescription)}>
            {isEditingDesc ? <ChevronDown className="h-4 w-4" /> : <AlignLeft className="h-4 w-4" />}
            {labels.editDescription}
          </button>

          <button type="button" role="menuitem" className={itemClass} onClick={run(onEditSoul)}>
            {isEditingSoul ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <span aria-hidden className="w-4 text-center text-xs font-bold">
                <Sparkles className="h-4 w-4" />
              </span>
            )}
            {labels.editSoul}
          </button>

          <button type="button" role="menuitem" className={itemClass} onClick={run(onManageSkills)}>
            <Package className="h-4 w-4" />
            {labels.manageSkills}
          </button>

          <button type="button" role="menuitem" className={itemClass} onClick={run(onCopyCommand)}>
            <Terminal className="h-4 w-4" />
            {labels.openInTerminal}
          </button>

          {!isDefault && (
            <button type="button" role="menuitem" className={itemClass} onClick={run(onRename)}>
              <Pencil className="h-4 w-4" />
              {labels.rename}
            </button>
          )}

          {!isDefault && (
            <button
              type="button"
              role="menuitem"
              className={cn(itemClass, 'text-destructive hover:bg-destructive/10')}
              onClick={run(onDelete)}
            >
              <Trash2 className="h-4 w-4" />
              {labels.delete}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

export default function ProfilesPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [profiles, setProfiles] = useState<ProfileInfo[]>([])
  const [activeInfo, setActiveInfo] = useState<ActiveProfileInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const { toast, showToast } = useToast()
  const { t, tr } = useI18n()
  const { setEnd, setTitle } = usePageHeader()
  const { refreshProfiles } = useProfileScope()

  // Locale strings with English fallbacks. The enriched keys are optional in
  // the i18n type so untranslated locales don't break the build — they render
  // the English literal until translated.
  const L = useMemo(() => {
    const p = t.profiles
    return {
      activeBadge: 'По умолчанию в терминале',
      setActive: 'По умолчанию в терминале',
      gatewayRunningWarning: p.gatewayRunningWarning ?? tr("This profile's gateway is running — it will be stopped."),
      aliasBadge: p.aliasBadge ?? tr('alias'),
      description: 'Описание для списка и канбана',
      descriptionPlaceholder:
        p.descriptionPlaceholder ??
        'Кратко опишите, какие задачи поручать этому агенту. Его роль в разговоре задаётся отдельно в «Роль и поведение».',
      noDescription: p.noDescription ?? tr('No description'),
      editDescription: 'Описание для списка и канбана',
      descriptionSaved: p.descriptionSaved ?? tr('Description saved'),
      reviewBadge: p.reviewBadge ?? tr('review'),
      autoGenerate: p.autoGenerate ?? tr('Auto-generate'),
      generating: p.generating ?? tr('Generating…'),
      describeFailed: p.describeFailed ?? tr('Could not generate description'),
      distribution: p.distribution ?? tr('Distribution'),
      modelLoading: p.modelLoading ?? tr('Loading models…'),
      modelNone: p.modelNone ?? tr('No authenticated providers — set a key first'),
      editModel: p.editModel ?? tr('Change model'),
      modelSaved: p.modelSaved ?? tr('Model updated'),
      modelSelect: p.modelSelect ?? tr('Select a model'),
      actions: p.actions ?? tr('Actions'),
      manageSkills: 'Навыки и инструменты'
    }
  }, [t.profiles, tr])

  // Модели загружаются при открытии редактора.
  const [modelChoices, setModelChoices] = useState<ModelChoice[] | null>(null)
  const modelChoicesLoading = useRef(false)

  // Inline rename state
  const [renamingFrom, setRenamingFrom] = useState<string | null>(null)
  const [renameTo, setRenameTo] = useState('')
  const [renameSaving, setRenameSaving] = useState(false)

  const addressedAgent = searchParams.get('agent')
  const addressedEditor = searchParams.get('edit')
  const addressedSection = searchParams.get('section')
  const editingLearningFor =
    ['role', 'learning'].includes(addressedEditor ?? '') && profiles.some(p => p.name === addressedAgent)
      ? addressedAgent
      : null
  const learningSection: LearningSection = ['role', 'memory', 'materials', 'corrections', 'check'].includes(addressedSection ?? '')
    ? (addressedSection as LearningSection)
    : 'role'

  // Inline description editor state
  const [editingDescFor, setEditingDescFor] = useState<string | null>(null)
  const [descText, setDescText] = useState('')
  const [descSaving, setDescSaving] = useState(false)
  const [describing, setDescribing] = useState(false)
  // Tracks the latest description request (save / auto-describe) so a late
  // response can't overwrite state for a different, newly-opened editor.
  const activeDescRequest = useRef<string | null>(null)
  // Counts in-flight save / auto-describe requests so the saving indicator
  // is only cleared when the last concurrent request settles.
  const descSavingCount = useRef(0)
  const describingCount = useRef(0)

  // Inline model editor state
  const [editingModelFor, setEditingModelFor] = useState<string | null>(null)
  const [modelEditChoice, setModelEditChoice] = useState('')
  const [modelSaving, setModelSaving] = useState(false)

  // Per-profile "set active" in-flight name
  const [settingActive, setSettingActive] = useState<string | null>(null)

  const loadModelChoices = useCallback(() => {
    if (modelChoices !== null || modelChoicesLoading.current) return
    modelChoicesLoading.current = true
    api
      .getModelOptions()
      .then(res => {
        setModelChoices(buildModelChoices(res.providers))
      })
      .catch(() => setModelChoices([]))
      .finally(() => {
        modelChoicesLoading.current = false
      })
  }, [modelChoices])

  const load = useCallback(() => {
    Promise.all([api.getProfiles(), api.getActiveProfile().catch(() => null)])
      .then(([res, active]) => {
        setProfiles(res.profiles)
        setActiveInfo(active)
      })
      .catch(e => showToast(ownerFacingError(e, 'Не удалось загрузить агентов.'), 'error'))
      .finally(() => setLoading(false))
  }, [showToast])

  useEffect(() => {
    load()
  }, [load])

  const isActive = useCallback(
    (p: ProfileInfo) =>
      activeInfo != null && (activeInfo.active === p.name || (activeInfo.active === 'default' && p.is_default)),
    [activeInfo]
  )

  const handleRenameSubmit = async () => {
    if (!renamingFrom || renameSaving) return
    const name = renamingFrom
    const target = renameTo.trim()
    if (!target || Array.from(target).length > 64) {
      showToast('Введите имя агента — до 64 символов.', 'error')
      return
    }
    setRenameSaving(true)
    try {
      const result = await api.updateProfileDisplayName(name, target)
      setProfiles(previous => previous.map(p => (p.name === name ? { ...p, display_name: result.display_name } : p)))
      showToast(`Имя сохранено: ${result.display_name}`, 'success')
      setRenamingFrom(null)
      setRenameTo('')
      void refreshProfiles().catch(() => {})
    } catch (e) {
      showToast(ownerFacingError(e, 'Не удалось сохранить имя агента.'), 'error')
    } finally {
      setRenameSaving(false)
    }
  }

  const handleSetActive = async (name: string) => {
    setSettingActive(name)
    try {
      // The backend normalizes/validates the name; trust the canonical
      // value it returns rather than the raw input.
      const { active } = await api.setActiveProfile(name)
      const selected = profiles.find(p => p.name === active)
      showToast(`Для новых запусков терминала выбран агент «${selected ? profileLabel(selected) : active}».`, 'success')
      setActiveInfo(prev => (prev ? { ...prev, active } : { active, current: active }))
    } catch (e) {
      showToast(ownerFacingError(e, 'Не удалось изменить активный профиль.'), 'error')
    } finally {
      setSettingActive(null)
    }
  }

  // Закрытие убирает адрес редактора, чтобы ссылка могла открыть его заново.
  const closeEditor = useCallback(() => {
    activeDescRequest.current = null
    setEditingModelFor(null)
    setEditingDescFor(null)
    setSearchParams(
      previous => {
        const next = new URLSearchParams(previous)
        next.delete('edit')
        next.delete('section')
        return next
      },
      { replace: true }
    )
  }, [setSearchParams])

  const openLearningAddress = (name: string) => {
    setEditingModelFor(null)
    setEditingDescFor(null)
    setSearchParams({ agent: name, edit: 'learning' })
  }

  const openDescEditor = useCallback(
    (p: ProfileInfo) => {
      if (editingDescFor === p.name) {
        closeEditor()
        return
      }
      closeEditor()
      activeDescRequest.current = p.name
      setEditingModelFor(null)
      setEditingDescFor(p.name)
      setDescText(p.description ?? '')
    },
    [closeEditor, editingDescFor]
  )

  const handleSaveDesc = async (name: string) => {
    descSavingCount.current += 1
    setDescSaving(true)
    activeDescRequest.current = name
    try {
      const res = await api.updateProfileDescription(name, descText)
      // Profile-list state always reflects the persisted result, but only
      // touch the open editor if it's still showing this profile.
      setProfiles(prev =>
        prev.map(p =>
          p.name === name
            ? {
                ...p,
                description: res.description,
                description_auto: res.description_auto
              }
            : p
        )
      )
      if (activeDescRequest.current === name) {
        showToast(`${L.descriptionSaved}: ${name}`, 'success')
        setEditingDescFor(null)
      }
    } catch (e) {
      if (activeDescRequest.current === name) {
        showToast(ownerFacingError(e, 'Не удалось создать описание профиля.'), 'error')
      }
    } finally {
      descSavingCount.current -= 1
      if (descSavingCount.current === 0) setDescSaving(false)
    }
  }

  const handleAutoDescribe = async (name: string) => {
    describingCount.current += 1
    setDescribing(true)
    activeDescRequest.current = name
    try {
      const res = await api.describeProfileAuto(name)
      const current = activeDescRequest.current === name
      if (res.ok && res.description != null) {
        if (current) setDescText(res.description)
        setProfiles(prev =>
          prev.map(p =>
            p.name === name
              ? {
                  ...p,
                  description: res.description ?? '',
                  description_auto: res.description_auto
                }
              : p
          )
        )
        if (current) showToast(`${L.descriptionSaved}: ${name}`, 'success')
      } else if (current) {
        showToast(ownerFacingError(res.reason, L.describeFailed), 'error')
      }
    } catch (e) {
      if (activeDescRequest.current === name) {
        showToast(ownerFacingError(e, 'Не удалось создать описание профиля.'), 'error')
      }
    } finally {
      describingCount.current -= 1
      if (describingCount.current === 0) setDescribing(false)
    }
  }

  const openModelEditor = useCallback(
    (p: ProfileInfo) => {
      if (editingModelFor === p.name) {
        closeEditor()
        return
      }
      if (addressedEditor !== 'model') closeEditor()
      setEditingDescFor(null)
      setEditingModelFor(p.name)
      setModelEditChoice(modelKey(p.provider, p.model))
      loadModelChoices()
    },
    [closeEditor, editingModelFor, loadModelChoices, addressedEditor]
  )

  const handleSaveModel = async (name: string) => {
    const picked = modelChoices?.find(c => choiceKey(c) === modelEditChoice)
    if (!picked) return
    setModelSaving(true)
    try {
      await api.setProfileModel(name, picked.provider, picked.model)
      showToast(`${L.modelSaved}: ${picked.model}`, 'success')
      setProfiles(prev =>
        prev.map(p => (p.name === name ? { ...p, model: picked.model, provider: picked.provider } : p))
      )
      setEditingModelFor(null)
    } catch (e) {
      showToast(ownerFacingError(e, 'Не удалось сохранить модель агента.'), 'error')
    } finally {
      setModelSaving(false)
    }
  }

  const openedAddress = useRef<string | null>(null)
  useEffect(() => {
    if (!addressedAgent || !addressedEditor) {
      openedAddress.current = null
      return
    }
    if (loading) return
    const address = `${addressedAgent}:${addressedEditor}:${addressedSection ?? ''}`
    if (openedAddress.current === address) return
    openedAddress.current = address
    const profile = profiles.find(item => item.name === addressedAgent)
    if (!profile) {
      showToast('Агент не найден. Выберите его в списке.', 'error')
      return
    }
    if (addressedEditor === 'model') openModelEditor(profile)
  }, [
    addressedAgent,
    addressedEditor,
    addressedSection,
    loading,
    profiles,
    openModelEditor,
    showToast
  ])

  // Exactly one editor is open at a time; derive which profile + kind so a
  // single dialog can render the right body.
  const editorName = editingModelFor ?? editingDescFor ?? editingLearningFor
  const editorKind: 'model' | 'desc' | 'learning' | null = editingModelFor
    ? 'model'
    : editingDescFor
      ? 'desc'
      : editingLearningFor
        ? 'learning'
        : null
  const editingProfile = profiles.find(p => p.name === editorName)
  const editorModalRef = useModalBehavior({
    open: editorName != null,
    onClose: closeEditor
  })

  const handleCopyTerminalCommand = async (name: string) => {
    let cmd: string
    try {
      const res = await api.getProfileSetupCommand(name)
      cmd = res.command
    } catch (e) {
      showToast(ownerFacingError(e, 'Не удалось подготовить команду профиля.'), 'error')
      return
    }
    if (await copyTextToClipboard(cmd)) {
      showToast(`${t.profiles.commandCopied}: ${cmd}`, 'success')
    } else {
      showToast(`${t.profiles.copyFailed}: ${cmd}`, 'error')
    }
  }

  const profileDelete = useConfirmDelete<string>({
    onDelete: useCallback(
      async (name: string) => {
        try {
          await api.deleteProfile(name)
          showToast('Агент удалён.', 'success')
          load()
          void refreshProfiles().catch(() => {})
        } catch (e) {
          showToast(ownerFacingError(e, 'Не удалось удалить агента.'), 'error')
          throw e
        }
      },
      [load, showToast, refreshProfiles]
    )
  })

  const pendingName = profileDelete.pendingId
  const pendingProfile = pendingName ? profiles.find(p => p.name === pendingName) : undefined
  const deleteMessage = (() => {
    const base =
      'Будут удалены его разговоры, сохранённые знания, навыки, настройки доступа и задачи по расписанию. Восстановить их после удаления нельзя.'
    return pendingProfile?.gateway_running ? `${base}\n\n${L.gatewayRunningWarning}` : base
  })()

  useLayoutEffect(() => {
    setTitle('Настройки агентов')
    setEnd(
      <Button size="sm" onClick={() => navigate('/profiles/new')}>
        Создать агента
      </Button>
    )
    return () => {
      setEnd(null)
      setTitle(null)
    }
  }, [setEnd, setTitle, navigate])

  if (loading) {
    return (
      <div aria-busy="true" aria-live="polite" className="flex items-center justify-center py-24">
        <span className="sr-only">{t.common.loading}</span>

        <ProfilesLoadingSpinner />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6">
      <Toast toast={toast} />

      <DeleteConfirmDialog
        open={profileDelete.isOpen}
        onCancel={profileDelete.cancel}
        onConfirm={profileDelete.confirm}
        title={`Удалить агента «${pendingProfile?.display_name?.trim() || pendingName || ''}»?`}
        description={deleteMessage}
        loading={profileDelete.isDeleting}
      />

      <p className="text-sm text-muted-foreground">
        Выберите агента, задайте правила работы и добавьте знания о своём деле. В обучении можно проверить результат
        вопросом, подключить инструменты и настроить расписание.
      </p>

      {/* List */}
      <div className="flex flex-col gap-3">
        <H2 variant="sm" className="flex items-center gap-2 text-muted-foreground">
          <Users className="h-4 w-4" />
          Все агенты ({profiles.length})
        </H2>

        {profiles.length === 0 && (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              {t.profiles.noProfiles}
            </CardContent>
          </Card>
        )}

        <div className="grid grid-cols-1 gap-8 sm:grid-cols-2 xl:grid-cols-3">
          {profiles.map(p => {
            const isRenaming = renamingFrom === p.name
            const isEditingSoul = editingLearningFor === p.name
            const isEditingDesc = editingDescFor === p.name
            const isEditingModel = editingModelFor === p.name
            const active = isActive(p)
            return (
              <Card key={p.name} className="h-full" data-depth="2">
                <CardContent className="flex h-full flex-col gap-2 py-4">
                  {isRenaming ? (
                    <div className="flex flex-col gap-2">
                      <Input
                        autoFocus
                        aria-label="Имя агента"
                        disabled={renameSaving}
                        value={renameTo}
                        onChange={e => setRenameTo(e.target.value)}
                        onKeyDown={e => {
                          if (e.key === 'Enter') handleRenameSubmit()
                          if (e.key === 'Escape' && !renameSaving) setRenamingFrom(null)
                        }}
                        aria-invalid={Array.from(renameTo.trim()).length > 64}
                      />

                      {(() => {
                        const trimmed = renameTo.trim()
                        const invalid = Array.from(trimmed).length > 64
                        return (
                          <p className={cn('text-xs', invalid ? 'text-destructive' : 'text-muted-foreground')}>
                            {invalid
                              ? 'Имя должно быть не длиннее 64 символов.'
                              : 'Имя на карточке и вкладке. Можно писать по-русски, до 64 символов.'}
                          </p>
                        )
                      })()}

                      <div className="flex gap-1.5">
                        <Button
                          size="sm"
                          disabled={renameSaving || !renameTo.trim() || Array.from(renameTo.trim()).length > 64}
                          onClick={() => void handleRenameSubmit()}
                        >
                          {renameSaving ? 'Сохраняю…' : t.common.save}
                        </Button>

                        <Button size="sm" ghost disabled={renameSaving} onClick={() => setRenamingFrom(null)}>
                          {t.common.cancel}
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="flex items-start gap-2">
                        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
                          <span className="font-medium text-sm truncate">{profileLabel(p)}</span>

                          {p.is_default && <Badge tone="secondary">Главный</Badge>}

                          {(() => {
                            const gw = gatewayBadge(p)
                            return (
                              <Badge
                                tone={gw.tone}
                                title={gw.title}
                                aria-label={`Состояние агента: ${gw.label}`}
                              >
                                {gw.label}
                              </Badge>
                            )
                          })()}
                        </div>

                        <ProfileActionsMenu
                          isActive={active}
                          isDefault={p.is_default}
                          isEditingDesc={isEditingDesc}
                          isEditingModel={isEditingModel}
                          isEditingSoul={isEditingSoul}
                          settingActive={settingActive === p.name}
                          labels={{
                            actions: L.actions,
                            setActive: L.setActive,
                            editModel: L.editModel,
                            editDescription: L.editDescription,
                            editSoul: 'Обучение и настройки',
                            manageSkills: L.manageSkills,
                            openInTerminal: t.profiles.openInTerminal,
                            rename: 'Изменить имя',
                            delete: t.common.delete
                          }}
                          onCopyCommand={() => handleCopyTerminalCommand(p.name)}
                          onDelete={() => profileDelete.requestDelete(p.name)}
                          onEditDescription={() => openDescEditor(p)}
                          onEditModel={() => openModelEditor(p)}
                          onEditSoul={() => openLearningAddress(p.name)}
                          onManageSkills={() => navigate(`/skills?profile=${encodeURIComponent(p.name)}`)}
                          onRename={() => {
                            if (renameSaving) return
                            setRenamingFrom(p.name)
                            setRenameTo(p.display_name?.trim() || p.name)
                          }}
                          onSetActive={() => handleSetActive(p.name)}
                        />
                      </div>

                      <div className="flex items-start gap-2 text-xs">
                        <span
                          className={cn(
                            'line-clamp-2',
                            p.description ? 'text-muted-foreground' : 'text-muted-foreground/60 italic'
                          )}
                        >
                          {p.description || L.noDescription}
                        </span>

                        {p.description && p.description_auto && (
                          <Badge tone="warning" className="shrink-0">
                            {L.reviewBadge}
                          </Badge>
                        )}
                      </div>

                      {p.model && (
                        <p className="mt-auto pt-2 text-xs text-muted-foreground truncate">Модель: {p.model}</p>
                      )}
                      <div className="flex flex-wrap gap-2 pt-3">
                        <Button size="sm" onClick={() => openLearningAddress(p.name)}>
                          Обучение и настройки
                        </Button>
                        <Button size="sm" ghost onClick={() => navigate(`/agents?agent=${encodeURIComponent(p.name)}`)}>
                          Открыть чат
                        </Button>
                      </div>
                      <details className="pt-2 text-xs text-muted-foreground">
                        <summary className="cursor-pointer py-1">Служебные сведения</summary>
                        <div className="grid gap-1 pt-2 break-all">
                          <span>Адрес агента: {p.name}</span>
                          <span>Каталог: {p.path}</span>
                          {p.provider && <span>Подключение модели: {p.provider}</span>}
                          <span>{gatewayBadge(p).detail}</span>
                          <span>Навыков и материалов: {p.skill_count}</span>
                          {active && <span>{L.activeBadge}</span>}
                          {p.has_alias && <span>Есть команда для запуска в терминале</span>}
                          {p.distribution_name && (
                            <span>
                              Сборка: {p.distribution_name} {p.distribution_version}
                            </span>
                          )}
                        </div>
                      </details>
                    </>
                  )}
                </CardContent>
              </Card>
            )
          })}
        </div>
      </div>

      {/* Один редактор роли: общая панель обучения, в том числе по старым ссылкам. */}
      {editorName && editingProfile && (
        <div
          ref={editorModalRef}
          className="fixed inset-0 z-[100] flex items-center justify-center bg-background/85 p-4"
          onClick={e => e.target === e.currentTarget && closeEditor()}
          role="dialog"
          aria-modal="true"
          aria-labelledby={editorKind === 'learning' ? undefined : 'profile-editor-title'}
          aria-label={editorKind === 'learning' ? `Обучение агента «${profileLabel(editingProfile)}»` : undefined}
        >
          <div
            className={cn(
              themedBody,
              'relative w-full bg-card shadow-2xl flex flex-col max-h-[90vh]',
              editorKind === 'learning' ? 'max-w-3xl' : 'max-w-lg'
            )}
          >
            {editorKind === 'learning' ? (
              <div className="p-5 min-h-0 overflow-y-auto">
                <ProfileLearningPanel
                  key={`${editorName}:${learningSection}`}
                  profile={editingProfile}
                  section={learningSection}
                  onClose={closeEditor}
                  onProfileChanged={load}
                />
              </div>
            ) : (
              <>
                <Button
                  ghost
                  size="icon"
                  onClick={closeEditor}
                  className="absolute right-2 top-2 text-muted-foreground hover:text-foreground"
                  aria-label={t.common.close}
                >
                  <X />
                </Button>

                <header className="p-5 pb-3 ">
                  <h2 id="profile-editor-title" className="text-base">
                    {editorKind === 'model' ? L.editModel : editorKind === 'desc' ? L.description : L.description}
                    <span className="text-muted-foreground">
                      {' '}
                      · {profiles.find(p => p.name === editorName)?.display_name || editorName}
                    </span>
                  </h2>
                </header>

                <div className="p-5 grid gap-4 min-h-0 overflow-y-auto">
                  {editorKind === 'model' &&
                    (modelChoices !== null && modelChoices.length === 0 ? (
                      <p className="text-xs text-muted-foreground">{L.modelNone}</p>
                    ) : (
                      <>
                        <Select
                          value={modelEditChoice}
                          disabled={modelChoices === null}
                          placeholder={modelChoices === null ? L.modelLoading : L.modelSelect}
                          onValueChange={setModelEditChoice}
                        >
                          {(modelChoices ?? []).map(c => (
                            <SelectOption key={choiceKey(c)} value={choiceKey(c)}>
                              {c.label}
                            </SelectOption>
                          ))}
                        </Select>

                        <div className="flex justify-end">
                          <Button
                            size="sm"
                            onClick={() => handleSaveModel(editorName)}
                            disabled={modelSaving || !modelChoices?.some(c => choiceKey(c) === modelEditChoice)}
                          >
                            {modelSaving ? t.common.saving : t.common.save}
                          </Button>
                        </div>
                      </>
                    ))}

                  {editorKind === 'desc' && (
                    <>
                      <div className="flex items-center justify-between gap-2">
                        <Label htmlFor="profile-desc-editor" className="text-xs text-muted-foreground">
                          {L.description}
                        </Label>

                        <Button
                          size="sm"
                          ghost
                          className="gap-1.5"
                          disabled={describing}
                          onClick={() => handleAutoDescribe(editorName)}
                        >
                          <Sparkles className="h-3.5 w-3.5" />
                          {describing ? L.generating : L.autoGenerate}
                        </Button>
                      </div>

                      <textarea
                        id="profile-desc-editor"
                        className="flex min-h-[96px] w-full bg-transparent px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none"
                        placeholder={L.descriptionPlaceholder}
                        value={descText}
                        onChange={e => setDescText(e.target.value)}
                      />

                      <div className="flex justify-end">
                        <Button size="sm" onClick={() => handleSaveDesc(editorName)} disabled={descSaving}>
                          {descSaving ? t.common.saving : t.common.save}
                        </Button>
                      </div>
                    </>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

interface ProfileActionsMenuProps {
  isActive: boolean
  isDefault: boolean
  isEditingDesc: boolean
  isEditingModel: boolean
  isEditingSoul: boolean
  labels: {
    actions: string
    delete: string
    editDescription: string
    editModel: string
    editSoul: string
    manageSkills: string
    openInTerminal: string
    rename: string
    setActive: string
  }
  settingActive: boolean
  onCopyCommand: () => void
  onDelete: () => void
  onEditDescription: () => void
  onEditModel: () => void
  onEditSoul: () => void
  onManageSkills: () => void
  onRename: () => void
  onSetActive: () => void
}
