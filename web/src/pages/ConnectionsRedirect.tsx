import { Navigate, useLocation } from "react-router";

/**
 * `/connections` — адрес отдельного экрана подключений из кандидата 0.21.13.
 * Решение Дмитрия 23.09: подключения живут в «Ключах и доступах», поэтому
 * ссылки на старый адрес ведут в раздел «Подключённые сервисы» с теми же
 * параметрами (например, `?connect=calendar&profile=default`).
 */
export default function ConnectionsRedirect() {
  const { search } = useLocation();
  return <Navigate to={`/env${search}#section-services`} replace />;
}
