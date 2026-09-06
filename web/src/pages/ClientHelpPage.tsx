import { useLayoutEffect, useRef, type ReactNode } from 'react';
import { ArrowLeft, ArrowRight, BookOpen, Check, ChevronRight, Clock3, Search } from 'lucide-react';
import { Link, useHref, useLocation, useNavigate, useParams, useSearchParams } from 'react-router';
import { usePageHeader } from '@/contexts/usePageHeader';
import { useTheme } from '@/themes';
import { HELP_ARTICLES, HELP_GROUPS, QUICK_HELP } from './help/articles';
import { helpPath, searchHelp, type HelpArticle, type HelpImage, type HelpLink } from './help/catalog';
import './help/help.css';

function ActionLink({ to, children, primary = false }: { to: string; children: ReactNode; primary?: boolean }) {
  return <Link to={to} className="help-action" data-primary={primary || undefined}>
    {children}<ArrowRight size={16} aria-hidden />
  </Link>;
}

function HelpLinks({ links }: { links: HelpLink[] }) {
  return <div className="help-links">{links.map(item => <ActionLink key={item.to} to={item.to}>{item.label}</ActionLink>)}</div>;
}

function HelpFigure({ illustration }: { illustration: HelpImage }) {
  const { themeName } = useTheme();
  const viewer = useRef<HTMLDialogElement>(null);
  // Роутер добавляет префикс кабинета и к статическим иллюстрациям.
  const src = useHref(`/help/${illustration.name}-${themeName === 'dark' ? 'dark' : 'light'}.webp`);
  return <details className="help-illustration">
    <summary>Посмотреть в интерфейсе<ChevronRight size={16} aria-hidden /></summary>
    <figure>
      <img src={src} alt={illustration.alt} width={illustration.width} height={illustration.height} loading="lazy" decoding="async" />
      <figcaption>{illustration.caption}</figcaption>
      <button type="button" className="help-action help-enlarge" onClick={() => viewer.current?.showModal()}>Увеличить снимок</button>
    </figure>
    <dialog ref={viewer} className="help-image-viewer" aria-label="Снимок интерфейса"
      onClick={event => { if (event.target === event.currentTarget) viewer.current?.close(); }}>
      <div className="help-image-toolbar"><p>Снимок можно прокручивать</p>
        <button type="button" className="help-action" onClick={() => viewer.current?.close()}>Закрыть снимок</button>
      </div>
      <div className="help-image-scroll"><img src={src} alt={illustration.alt} width={illustration.width} height={illustration.height} loading="lazy" /></div>
    </dialog>
  </details>;
}

function HelpIndex() {
  const [params, setParams] = useSearchParams();
  const query = params.get('q') ?? '';
  const filtered = searchHelp(HELP_ARTICLES, query);
  return <>
    <header className="help-welcome">
      <div className="help-welcome-copy">
        <p className="help-eyebrow"><BookOpen size={17} aria-hidden />Как здесь работать</p>
        <h2 id="help-heading" tabIndex={-1}>Что вы хотите сделать?</h2>
        <p>Короткие инструкции: куда нажать, что написать и какой результат ждать.</p>
      </div>
      <Link to="/help/start" className="help-start">
        <span className="help-start-icon"><Clock3 size={23} aria-hidden /></span>
        <span><strong>Первые 10 минут</strong><span>От первого сообщения к полезному результату</span></span>
        <ArrowRight size={20} aria-hidden />
      </Link>
    </header>
    <section className="help-search" aria-label="Поиск по помощи">
      <label htmlFor="help-search">Найти инструкцию</label>
      <div className="help-search-field">
        <Search size={20} aria-hidden />
        <input id="help-search" type="search" placeholder="Например: голос, роль агента или Telegram" value={query}
          onChange={event => setParams(event.target.value ? { q: event.target.value } : {}, { replace: true })} />
        {query && <button type="button" onClick={() => setParams({}, { replace: true })}>Очистить</button>}
      </div>
      {query && <p role="status">{filtered.length ? `Найдено инструкций: ${filtered.length}` : 'По этому запросу ничего не найдено. Попробуйте «файлы», «голос» или «не отвечает».'}</p>}
    </section>
    {!query.trim() && <section aria-labelledby="help-popular">
      <h3 id="help-popular">Частые задачи</h3>
      <div className="help-quick-grid">{QUICK_HELP.map(item => <Link key={item.to} to={item.to} className="help-quick">
        <span><strong>{item.title}</strong><span>{item.detail}</span></span><ArrowRight size={18} aria-hidden />
      </Link>)}</div>
    </section>}
    {HELP_GROUPS.map(group => {
      const articles = filtered.filter(article => article.group === group);
      return articles.length > 0 && <section key={group} aria-label={group}>
        <h3>{group}</h3>
        <div className="help-catalog">{articles.map(article => <Link key={article.id} to={helpPath(article.id)} className="help-catalog-item">
          <span><strong>{article.label}</strong><span>{article.title}</span></span><ChevronRight size={17} aria-hidden />
        </Link>)}</div>
      </section>;
    })}
    {filtered.length === 0 && <HelpLinks links={[{ to: '/help/troubleshooting', label: 'Помочь с неполадкой' }, { to: '/help', label: 'Показать все инструкции' }]} />}
  </>;
}

function HelpNavigation({ current }: { current: HelpArticle }) {
  const navigate = useNavigate();
  return <>
    <div className="help-mobile-nav">
      <label htmlFor="help-page-select">Другие инструкции</label>
      <select id="help-page-select" value={current.id} onChange={event => void navigate(helpPath(event.target.value))}>
        {HELP_GROUPS.map(group => <optgroup key={group} label={group}>
          {HELP_ARTICLES.filter(article => article.group === group).map(article => <option key={article.id} value={article.id}>{article.label}</option>)}
        </optgroup>)}
      </select>
    </div>
    <aside className="help-sidebar">
      <nav aria-label="Все инструкции">
        <Link to="/help" className="help-back"><ArrowLeft size={16} aria-hidden />Оглавление и поиск</Link>
        {HELP_GROUPS.map(group => <div key={group} className="help-nav-group">
          <p>{group}</p>
          {HELP_ARTICLES.filter(article => article.group === group).map(article => <Link key={article.id} to={helpPath(article.id)}
            aria-current={current.id === article.id ? 'page' : undefined}>{article.label}</Link>)}
        </div>)}
      </nav>
    </aside>
  </>;
}

function HelpArticleView({ article }: { article: HelpArticle }) {
  const related = article.related.map(id => HELP_ARTICLES.find(item => item.id === id)).filter(item => item !== undefined);
  return <>
    <nav aria-label="Путь в помощи" className="help-breadcrumbs">
      <Link to="/help"><ArrowLeft size={16} aria-hidden />Вся помощь</Link><ChevronRight size={14} aria-hidden /><span>{article.label}</span>
    </nav>
    <div className="help-article-layout">
      <HelpNavigation current={article} />
      <article className="help-article" aria-labelledby="help-heading">
        <header className="help-article-header">
          <p className="help-eyebrow">{article.label}</p>
          <h2 id="help-heading" tabIndex={-1}>{article.title}</h2>
          <p>{article.summary}</p>
          <ActionLink to={article.action.to} primary>{article.action.label}</ActionLink>
        </header>
        <nav className="help-on-page" aria-label="На этой странице">
          <p>В этой инструкции</p>
          <ol>{article.sections.map(section => <li key={section.id}><Link to={`${helpPath(article.id)}#${section.id}`}>{section.title}</Link></li>)}</ol>
        </nav>
        {article.sections.map(section => <section key={section.id} id={section.id} className="help-procedure" aria-labelledby={`title-${section.id}`} tabIndex={-1}>
          <h3 id={`title-${section.id}`}>{section.title}</h3>
          {section.intro && <p className="help-muted">{section.intro}</p>}
          <ol className="help-steps">{section.steps.map((item, index) => <li key={item.title}>
            <span className="help-step-number" aria-hidden>{index + 1}</span>
            <div><h4>{item.title}</h4><p>{item.action}</p>
              <p className="help-result"><Check size={15} aria-hidden /><span><strong>Что вы увидите: </strong>{item.result}</span></p>
            </div>
          </li>)}</ol>
          {section.example && <aside className="help-example" aria-label="Пример поручения"><p>Пример — замените детали своими</p><blockquote>{section.example}</blockquote></aside>}
          {section.note && <aside className="help-note"><p>{section.note}</p></aside>}
          {section.image && <HelpFigure illustration={section.image} />}
          {section.links && <HelpLinks links={section.links} />}
        </section>)}
        <section className="help-problems" aria-labelledby="help-problems-title">
          <h3 id="help-problems-title">Если не получилось</h3>
          {article.problems.map(problem => <details key={problem.question}>
            <summary>{problem.question}<ChevronRight size={17} aria-hidden /></summary>
            <p>{problem.answer}</p>
            {problem.link && <HelpLinks links={[problem.link]} />}
          </details>)}
        </section>
        <footer className="help-related">
          <h3>Может пригодиться</h3>
          <HelpLinks links={related.map(item => ({ label: item.label, to: helpPath(item.id) }))} />
          <Link to="/help" className="help-back"><ArrowLeft size={16} aria-hidden />Вернуться к оглавлению</Link>
        </footer>
      </article>
    </div>
  </>;
}

export default function ClientHelpPage() {
  const { article: articleId } = useParams();
  const location = useLocation();
  const { setTitle } = usePageHeader();
  const container = useRef<HTMLDivElement>(null);
  const article = HELP_ARTICLES.find(item => item.id === articleId);
  useLayoutEffect(() => {
    setTitle('Помощь');
    return () => setTitle(null);
  }, [setTitle, location.pathname]);
  useLayoutEffect(() => {
    // Прокручиваем основной контейнер панели, а не окно: он живёт внутри кабинета.
    let id = '';
    try { id = decodeURIComponent(location.hash.slice(1)); } catch { /* Повреждённая ссылка открывает начало инструкции. */ }
    const target = (id ? document.getElementById(id) : null) ?? container.current?.querySelector<HTMLElement>('#help-heading');
    if (target && container.current?.contains(target)) {
      const scroller = container.current.closest('main');
      if (!id && scroller) scroller.scrollTop = 0;
      else (id ? target : container.current).scrollIntoView({ block: 'start' });
      target.focus({ preventScroll: true });
    }
  }, [location.pathname, location.hash]);
  return <div ref={container} className="korra-help">
    {!articleId ? <HelpIndex /> : article ? <HelpArticleView key={article.id} article={article} /> : <section className="help-missing">
      <h2 id="help-heading" tabIndex={-1}>Такой инструкции пока нет</h2>
      <p>Откройте оглавление или найдите нужное действие через поиск.</p>
      <ActionLink to="/help" primary>Оглавление и поиск</ActionLink>
    </section>}
  </div>;
}
