/**
 * Окна и меню панели рисуются порталом в <body>, вне корня приложения с
 * `data-client-ui`. Правила «один шрифт на весь интерфейс» (index.css,
 * решение владельца 03.09) написаны для потомков этой метки, поэтому без неё
 * на body порталы получали шрифты набора Hermes: латиница с засечками в окне
 * выбора браузера. Возвращает функцию, снимающую метку.
 */
export function markClientUiBody(body: HTMLElement = document.body): () => void {
  body.dataset.clientUi = "true";
  return () => {
    delete body.dataset.clientUi;
  };
}
