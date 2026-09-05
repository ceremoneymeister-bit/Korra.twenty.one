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

// Mirrors korra_cli/profiles.py::_PROFILE_ID_RE so we can reject obviously
// invalid names (uppercase, spaces, …) before round-tripping a doomed POST.
const PROFILE_NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/

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
                S
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
  const { setProfile } = useProfileScope()

  // Locale strings with English fallbacks. The enriched keys are optional in
  // the i18n type so untranslated locales don't break the build — they render
  // the English literal until translated.
  const L = useMemo(() => {
    const p = t.profiles
    return {
      activeProfile: p.activeProfile ?? tr('Active profile'),
      activeBadge: p.activeBadge ?? tr('active'),
      setActive: p.setActive ?? tr('Set as active'),
      activeSet: p.activeSet ?? tr('Active profile set'),
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
      manageSkills: p.manageSkills ?? tr('Manage skills & tools'),
      activeSetHint:
        p.activeSetHint ?? tr('Dashboard switched to manage {name}. New CLI/gateway runs will use this profile too.')
    }
  }, [t.profiles, tr])

  // Модели загружаются при открытии редактора.
  const [modelChoices, setModelChoices] = useState<ModelChoice[] | null>(null)
  const modelChoicesLoading = useRef(false)

  // Inline rename state
  const [renamingFrom, setRenamingFrom] = useState<string | null>(null)
  const [renameTo, setRenameTo] = useState('')

  // Inline SOUL editor state
  const [editingSoulFor, setEditingSoulFor] = useState<string | null>(null)
  const [soulText, setSoulText] = useState('')
  const [soulSaving, setSoulSaving] = useState(false)
  // Tracks the latest SOUL request so out-of-order responses don't overwrite
  // newer state when the user switches profiles or closes the editor.
  const activeSoulRequest = useRef(0)
  const [soulLoadState, setSoulLoadState] = useState<'loading' | 'ready' | 'error'>('loading')

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
    if (!renamingFrom) return
    const target = renameTo.trim()
    if (!target || target === renamingFrom) {
      setRenamingFrom(null)
      setRenameTo('')
      return
    }
    if (!PROFILE_NAME_RE.test(target)) {
      showToast(`${t.profiles.invalidName}: ${t.profiles.nameRule}`, 'error')
      return
    }
    try {
      await api.renameProfile(renamingFrom, target)
      showToast(`${t.profiles.renamed}: ${renamingFrom} → ${target}`, 'success')
      setRenamingFrom(null)
      setRenameTo('')
      load()
    } catch (e) {
      showToast(ownerFacingError(e, 'Не удалось переименовать агента.'), 'error')
    }
  }

  const handleSetActive = async (name: string) => {
    setSettingActive(name)
    try {
      // The backend normalizes/validates the name; trust the canonical
      // value it returns rather than the raw input.
      const { active } = await api.setActiveProfile(name)
      setProfile(active)
      showToast(`${L.activeSet}: ${active} — ${L.activeSetHint.replace('{name}', active)}`, 'success')
      setActiveInfo(prev => (prev ? { ...prev, active } : { active, current: active }))
    } catch (e) {
      showToast(ownerFacingError(e, 'Не удалось изменить активный профиль.'), 'error')
    } finally {
      setSettingActive(null)
    }
  }

  // Закрытие убирает адрес редактора, чтобы ссылка могла открыть его заново.
  const closeEditor = useCallback(() => {
    activeSoulRequest.current += 1
    activeDescRequest.current = null
    setEditingModelFor(null)
    setEditingDescFor(null)
    setEditingSoulFor(null)
    setSearchParams(
      previous => {
        const next = new URLSearchParams(previous)
        next.delete('edit')
        return next
      },
      { replace: true }
    )
  }, [setSearchParams])

  const loadSoul = useCallback(async (name: string) => {
    const ticket = ++activeSoulRequest.current
    setSoulText('')
    setSoulLoadState('loading')
    try {
      const soul = await api.getProfileSoul(name)
      if (activeSoulRequest.current !== ticket) return
      setSoulText(soul.content)
      setSoulLoadState('ready')
    } catch {
      if (activeSoulRequest.current === ticket) setSoulLoadState('error')
    }
  }, [])

  const openSoulEditor = useCallback(
    (name: string) => {
      if (editingSoulFor === name) {
        closeEditor()
        return
      }
      setEditingDescFor(null)
      setEditingModelFor(null)
      setEditingSoulFor(name)
      void loadSoul(name)
    },
    [closeEditor, editingSoulFor, loadSoul]
  )

  const handleSaveSoul = async (name: string) => {
    if (soulLoadState !== 'ready' || soulSaving) return
    const ticket = activeSoulRequest.current
    setSoulSaving(true)
    try {
      await api.updateProfileSoul(name, soulText)
      if (activeSoulRequest.current === ticket) {
        showToast('Роль сохранена. Изменения применятся в новом разговоре.', 'success')
        closeEditor()
      }
    } catch (e) {
      if (activeSoulRequest.current === ticket) {
        showToast(ownerFacingError(e, 'Не удалось сохранить роль агента.'), 'error')
      }
    } finally {
      setSoulSaving(false)
    }
  }

  const openDescEditor = useCallback(
    (p: ProfileInfo) => {
      if (editingDescFor === p.name) {
        closeEditor()
        return
      }
      activeDescRequest.current = p.name
      activeSoulRequest.current += 1
      setEditingSoulFor(null)
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
      activeSoulRequest.current += 1
      setEditingSoulFor(null)
      setEditingDescFor(null)
      setEditingModelFor(p.name)
      setModelEditChoice(modelKey(p.provider, p.model))
      loadModelChoices()
    },
    [closeEditor, editingModelFor, loadModelChoices]
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

  const addressedAgent = searchParams.get('agent')
  const addressedEditor = searchParams.get('edit')
  const openedAddress = useRef<string | null>(null)
  useEffect(() => {
    if (!addressedAgent || !addressedEditor) {
      openedAddress.current = null
      return
    }
    if (loading) return
    const address = `${addressedAgent}:${addressedEditor}`
    if (openedAddress.current === address) return
    openedAddress.current = address
    const profile = profiles.find(item => item.name === addressedAgent)
    if (!profile) {
      showToast('Агент не найден. Выберите его в списке.', 'error')
      return
    }
    if (addressedEditor === 'role') openSoulEditor(profile.name)
    if (addressedEditor === 'model') openModelEditor(profile)
  }, [addressedAgent, addressedEditor, loading, profiles, openSoulEditor, openModelEditor, showToast])

  // Exactly one editor is open at a time; derive which profile + kind so a
  // single dialog can render the right body.
  const editorName = editingModelFor ?? editingDescFor ?? editingSoulFor
  const editorKind: 'model' | 'desc' | 'soul' | null = editingModelFor
    ? 'model'
    : editingDescFor
      ? 'desc'
      : editingSoulFor
        ? 'soul'
        : null
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
          showToast(`${t.profiles.deleted}: ${name}`, 'success')
          load()
        } catch (e) {
          showToast(ownerFacingError(e, 'Не удалось удалить агента.'), 'error')
          throw e
        }
      },
      [load, showToast, t.profiles.deleted]
    )
  })

  const pendingName = profileDelete.pendingId
  const pendingProfile = pendingName ? profiles.find(p => p.name === pendingName) : undefined
  const deleteMessage = (() => {
    if (!pendingName) return t.profiles.confirmDeleteMessage
    const base = t.profiles.confirmDeleteMessage.replace('{name}', pendingName)
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
        title={t.profiles.confirmDeleteTitle}
        description={deleteMessage}
        loading={profileDelete.isDeleting}
      />

      {/* Active profile banner */}
      {activeInfo && (
        <Card>
          <CardContent className="flex flex-wrap items-center gap-x-4 gap-y-1 py-3 text-xs">
            <span className="flex items-center gap-2 text-muted-foreground">
              <Check className="h-3.5 w-3.5 text-success" />

              <span>
                {L.activeProfile}: <span className="font-medium text-foreground">{activeInfo.active}</span>
              </span>
            </span>

            {activeInfo.current !== activeInfo.active && (
              <span className="text-muted-foreground/80">({activeInfo.current})</span>
            )}
          </CardContent>
        </Card>
      )}

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
            const isEditingSoul = editingSoulFor === p.name
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
                        value={renameTo}
                        onChange={e => setRenameTo(e.target.value)}
                        onKeyDown={e => {
                          if (e.key === 'Enter') handleRenameSubmit()
                          if (e.key === 'Escape') setRenamingFrom(null)
                        }}
                        aria-invalid={
                          renameTo.trim() !== '' && renameTo.trim() !== p.name && !PROFILE_NAME_RE.test(renameTo.trim())
                        }
                      />

                      {(() => {
                        const trimmed = renameTo.trim()
                        const invalid = trimmed !== '' && trimmed !== p.name && !PROFILE_NAME_RE.test(trimmed)
                        return (
                          <p className={cn('text-xs', invalid ? 'text-destructive' : 'text-muted-foreground')}>
                            {invalid ? `${t.profiles.invalidName}: ${t.profiles.nameRule}` : t.profiles.nameRule}
                          </p>
                        )
                      })()}

                      <div className="flex gap-1.5">
                        <Button size="sm" onClick={handleRenameSubmit}>
                          {t.common.save}
                        </Button>

                        <Button size="sm" ghost onClick={() => setRenamingFrom(null)}>
                          {t.common.cancel}
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="flex items-start gap-2">
                        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
                          <span className="font-medium text-sm truncate">
                            {p.display_name?.trim() ? `${p.display_name.trim()} (${p.name})` : p.name}
                          </span>

                          {active && <Badge tone="success">{L.activeBadge}</Badge>}

                          {p.is_default && <Badge tone="secondary">{t.profiles.defaultBadge}</Badge>}

                          {p.has_alias && <Badge tone="outline">{L.aliasBadge}</Badge>}

                          {p.distribution_name && (
                            <Badge tone="outline" className="gap-1">
                              <Package className="h-3 w-3" />
                              {p.distribution_name}
                              {p.distribution_version ? `@${p.distribution_version}` : ''}
                            </Badge>
                          )}
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
                            editSoul: 'Роль и поведение',
                            manageSkills: L.manageSkills,
                            openInTerminal: t.profiles.openInTerminal,
                            rename: t.profiles.rename,
                            delete: t.common.delete
                          }}
                          onCopyCommand={() => handleCopyTerminalCommand(p.name)}
                          onDelete={() => profileDelete.requestDelete(p.name)}
                          onEditDescription={() => openDescEditor(p)}
                          onEditModel={() => openModelEditor(p)}
                          onEditSoul={() => openSoulEditor(p.name)}
                          onManageSkills={() => navigate(`/skills?profile=${encodeURIComponent(p.name)}`)}
                          onRename={() => {
                            setRenamingFrom(p.name)
                            setRenameTo(p.name)
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

                      <div className="mt-auto flex flex-col gap-0.5 pt-1 text-xs text-muted-foreground">
                        {p.model && (
                          <span className="truncate">
                            {t.profiles.model}: {p.model}
                            {p.provider ? ` (${p.provider})` : ''}
                          </span>
                        )}

                        <span>
                          {t.profiles.skills}: {p.skill_count}
                        </span>

                        <span className="truncate">{p.path}</span>
                      </div>
                      <div className="flex flex-wrap gap-2 pt-3">
                        <Button size="sm" onClick={() => navigate(`/agents?agent=${encodeURIComponent(p.name)}`)}>
                          Открыть чат
                        </Button>
                        <Button size="sm" ghost onClick={() => openSoulEditor(p.name)}>
                          Роль и поведение
                        </Button>
                      </div>
                    </>
                  )}
                </CardContent>
              </Card>
            )
          })}
        </div>
      </div>

      {/* Editor dialog — model / description / SOUL for the selected profile */}
      {editorName && (
        <div
          ref={editorModalRef}
          className="fixed inset-0 z-[100] flex items-center justify-center bg-background/85 p-4"
          onClick={e => e.target === e.currentTarget && closeEditor()}
          role="dialog"
          aria-modal="true"
          aria-labelledby="profile-editor-title"
        >
          <div className={cn(themedBody, 'relative w-full max-w-lg bg-card shadow-2xl flex flex-col max-h-[90vh]')}>
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
                {editorKind === 'model' ? L.editModel : editorKind === 'desc' ? L.description : 'Роль и поведение'}
                <span className="text-muted-foreground">
                  {' '}
                  · {profiles.find(p => p.name === editorName)?.display_name || editorName}
                </span>
              </h2>
            </header>

            <div className={cn('p-5 grid gap-4', editorKind === 'soul' && 'min-h-0 overflow-y-auto')}>
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

              {editorKind === 'soul' && (
                <>
                  <Label htmlFor="profile-soul-editor" className="text-xs text-muted-foreground">
                    {'Роль и поведение'}
                  </Label>

                  <p className="text-sm text-muted-foreground">
                    Напишите обычными словами, что агент делает, какие правила соблюдает и что должен знать о вашем
                    бизнесе. Изменения роли применятся в новом разговоре.
                  </p>
                  {soulLoadState === 'loading' && <p role="status">Загружаем роль…</p>}
                  {soulLoadState === 'error' && (
                    <div role="alert" className="grid gap-3 text-sm">
                      <p>Не удалось загрузить роль. Повторите загрузку, чтобы сохранить ваши инструкции.</p>
                      <Button size="sm" onClick={() => void loadSoul(editorName)}>
                        Повторить загрузку
                      </Button>
                    </div>
                  )}
                  <textarea
                    id="profile-soul-editor"
                    disabled={soulLoadState !== 'ready' || soulSaving}
                    className="flex min-h-[280px] w-full bg-transparent px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none"
                    placeholder="Опишите задачи агента, правила работы и важные факты о вашем бизнесе."
                    value={soulText}
                    onChange={e => setSoulText(e.target.value)}
                  />

                  <div className="flex justify-end">
                    <Button
                      size="sm"
                      onClick={() => void handleSaveSoul(editorName)}
                      disabled={soulSaving || soulLoadState !== 'ready'}
                    >
                      {soulSaving ? t.common.saving : t.common.save}
                    </Button>
                  </div>
                </>
              )}
            </div>
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
