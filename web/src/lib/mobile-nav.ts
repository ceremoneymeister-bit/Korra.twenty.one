import { atom } from "nanostores";

/**
 * Открыто ли выдвижное меню ☰ на телефоне.
 *
 * Раньше меню открывала только фиксированная шапка «› KORRA». На экране
 * агентов её больше нет: ☰ стоит в строке разговора, внутри экрана, поэтому
 * состояние меню — общее, а не локальное состояние App.
 */
export const $mobileNavOpen = atom(false);

export function openMobileNav(): void {
  $mobileNavOpen.set(true);
}

export function closeMobileNav(): void {
  $mobileNavOpen.set(false);
}
