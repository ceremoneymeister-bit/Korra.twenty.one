import {
  Select,
  SelectOption,
} from "@nous-research/ui/ui/components/select";
import { useProfileScope } from "@/contexts/useProfileScope";
import { useI18n } from "@/i18n";

function profileLabel(template: string, name: string): string {
  return template.replace("{name}", name);
}

/** Section-local profile selector rendered next to the page title. */
export function ProfileScopeChip() {
  const { profile, currentProfile, profiles, setProfile } = useProfileScope();
  const { t } = useI18n();

  if (profiles.length < 2) return null;

  const template = t.app.profileScopeLabel ?? "Profile: {name}";
  const selected = profile || currentProfile || "default";

  return (
    <Select
      className="w-auto min-w-36 max-w-64 shrink-0 [&_.neo-select-trigger]:h-7 [&_.neo-select-trigger]:min-h-7 [&_.neo-select-trigger]:w-auto [&_.neo-select-trigger]:max-w-64 [&_.neo-select-trigger]:rounded-[var(--neo-radius-round)] [&_.neo-select-trigger]:border-0 [&_.neo-select-trigger]:bg-[var(--neo-surface)] [&_.neo-select-trigger]:px-3 [&_.neo-select-trigger]:py-1 [&_.neo-select-trigger]:text-xs [&_.neo-select-trigger]:text-[var(--neo-text-primary)] [&_.neo-select-trigger]:outline-0 [&_.neo-select-trigger]:shadow-[var(--neo-depth-1)] [&_.neo-select-trigger:focus-visible]:outline-0"
      id="korra-profile-scope"
      onValueChange={setProfile}
      value={selected}
    >
      {profiles.map((item) => {
        const name = item.display_name?.trim() || item.name;
        return (
          <SelectOption key={item.name} value={item.name}>
            {profileLabel(template, name)}
          </SelectOption>
        );
      })}
    </Select>
  );
}
