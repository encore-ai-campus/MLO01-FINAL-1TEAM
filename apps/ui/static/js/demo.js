"use strict";

const sourceCards = (() => {
      const template = {
        source_tier:2, doc_type:"standard_template", title:"공공기관 출장 정산 양식",
        keywords:"출장 출장비 정산 신청서 공공 표준 양식 제출 지출 증빙 여비", excerpt:"출장 기간, 여비 내역, 첨부 증빙을 작성하는 참고 양식입니다."
      };
      const benchmark = {
        source_tier:2, doc_type:"benchmark_reference", title:"외부 기관 출장비 운영 사례",
        keywords:"출장 출장비 정산 외부 사례 벤치마크 여비", excerpt:"기관별 출장비 정산 항목과 승인 절차를 비교하는 참고 사례입니다."
      };
      const notices = [{
        type:"discoverable", title:"해외출장 경비 정산 세칙", department:"재무팀",
        message:"열람이 필요하면 재무팀에 협조전을 제출해 주세요.", keywords:"출장 출장비 경비 정산 해외출장"
      }];
      const ontology_graph_meta = {
        conflicts:[{
          target_doc_id:"COMMON-002", target_title:"전사 위임전결 규정",
          clause_pair:"구매 업무 세칙 제3조(구매 한도) ↔ 전사 위임전결 규정 제5조(구매 한도)",
          priority_rule:"부서 최신 세칙을 우선 적용합니다."
        }]
      };
      const labels = {
        official:"사내 공식 사규",
        template:"공공 표준 양식 · 사내 규정 아님",
        benchmark:"외부 참고 사례 · 사내 규정 아님"
      };
      const icons = {
        template:'<circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-8h.01"/>',
        benchmark:'<path d="M13 5h6v6m0-6-8 8m6 0v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1h5"/>',
        warning:'<path d="m12 3 10 18H2L12 3Zm0 6v5m0 3h.01"/>'
      };
      const icon = name => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + icons[name] + '</svg>';
      function kind(source) {
        if (source.source_tier === 1 && ["official_policy","ojt_guide"].includes(source.doc_type)) return "official";
        if (source.source_tier === 2 && source.doc_type === "standard_template") return "template";
        if (source.source_tier === 2 && source.doc_type === "benchmark_reference") return "benchmark";
      }
      function badge(source) {
        const type = kind(source);
        return '<span class="source-badge" data-source="' + type + '">' +
          (icons[type] ? icon(type) : "") + labels[type] + '</span>';
      }
      function renderTextCards(container, items, className, content) {
        container.replaceChildren(...items.map(item => {
          const card = document.createElement("article");
          card.className = className;
          content(card,item);
          return card;
        }));
      }
      function appendText(card, tag, value, className) {
        const element = document.createElement(tag);
        element.textContent = value;
        if (className) element.className = className;
        card.append(element);
      }
      function renderSources(container, sources) {
        renderTextCards(container,sources.filter(kind),"source-card",(card,source) => {
          card.dataset.source = kind(source);
          card.innerHTML = badge(source);
          appendText(card,"strong",source.title);
          appendText(card,"p",source.excerpt);
          if (kind(source) === "benchmark") appendText(card,"p","타사/타기관 참고 사례입니다","source-fixed-label");
        });
      }
      function renderNotices(container, items) {
        renderTextCards(container,items.filter(item => item.type === "discoverable"),"source-notice",(card,item) => {
          appendText(card,"strong","협조전 안내 · " + item.title);
          appendText(card,"p","소관 부서 · " + item.department);
          appendText(card,"p",item.message);
        });
      }
      function renderConflicts(container, sources) {
        const pairs = sources.flatMap(source => source.ontology_graph_meta?.conflicts ?? []);
        renderTextCards(container,pairs.filter(pair => pair.target_doc_id && pair.clause_pair),"source-conflict",(card,pair) => {
          appendText(card,"strong","규정 충돌 · " + pair.target_title);
          appendText(card,"p",pair.clause_pair);
          appendText(card,"p",pair.priority_rule);
        });
      }
      function matches(source, query) {
        return query.trim().toLowerCase().split(/\s+/).filter(Boolean).every(word => source.keywords.includes(word));
      }
      function renderHeader(container, header, sources) {
        container.replaceChildren();
        container.hidden = !header || sources.some(source => source.source_tier === 1);
        if (container.hidden) return;
        container.innerHTML = icon("warning");
        container.append(header);
      }
      return {template,benchmark,notices,ontology_graph_meta,kind,badge,matches,renderSources,renderNotices,renderConflicts,renderHeader};
    })();
    sourceCards.renderSources(document.getElementById("builder-benchmark"),[sourceCards.benchmark]);

(() => {
    const root = document.getElementById("question-desk-home");


    const examples = {
      expense: {
        source_tier:1, doc_type:"official_policy",
        keywords:["출장", "정산", "영수증", "증빙", "경비"],
        question:"출장비 정산에 어떤 서류가 필요해요?",
        answer:"출장비 정산 신청서에 지출 영수증을 첨부해 주세요.",
        document:"출장비 규정 · 제4조",
        excerpt:"출장비 정산 신청 시 지출 영수증을 첨부한다.",
        owned:true
      },
      leave: {
        source_tier:1, doc_type:"official_policy",
        keywords:["연차", "휴가", "근태"],
        question:"연차는 어디에서 신청하나요?",
        answer:"그룹웨어의 근태 메뉴에서 휴가 신청서를 작성해 주세요.",
        document:"복무 규정 · 연차 신청",
        excerpt:"연차는 그룹웨어 근태 메뉴를 통해 신청한다.",
        owned:false
      },
      approval: {
        source_tier:1, doc_type:"official_policy",
        keywords:["결재", "승인", "전결"],
        question:"품의서를 올릴 때 결재선은 어디서 확인해요?",
        answer:"위임전결 규정의 업무별 결재 기준표에서 해당 업무의 결재선을 확인해 주세요.",
        document:"위임전결 규정 · 결재 기준표",
        excerpt:"업무별 결재권자는 위임전결 기준표에서 정한다.",
        owned:true
      },
      purchase: {
        source_tier:1, doc_type:"official_policy",
        keywords:["구매 한도","구매한도"],
        question:"구매 한도가 어떻게 달라요?",
        answer:"구매 업무 세칙의 한도와 전사 위임전결 규정의 한도를 함께 확인해 주세요.",
        document:"구매 업무 세칙 · 제3조",
        excerpt:"구매 업무의 적용 한도는 부서 세칙 기준을 따른다.",
        ontology_graph_meta:sourceCards.ontology_graph_meta,
        owned:true
      }
    };
    const question = root.querySelector("#desk-question");
    const keyword = root.querySelector("#desk-keyword");
    const ownDepartment = root.querySelector("#desk-own");
    const includeBenchmark = root.querySelector("#desk-qa-benchmark");
    const banner = root.querySelector("#desk-source-banner");
    const externalSources = root.querySelector("#desk-related-sources");
    const qaNotices = root.querySelector("#desk-source-notices");
    const qaConflicts = root.querySelector("#desk-source-conflicts");
    const title = root.querySelector("#desk-result-title");
    const caption = root.querySelector("#desk-result-caption");
    const copy = root.querySelector("#desk-result-copy");
    const evidence = root.querySelector("#desk-evidence");
    const sourceTitle = root.querySelector("#desk-source-title");
    const sourceExcerpt = root.querySelector("#desk-source-excerpt");
    const fullSource = root.querySelector("#desk-full-source");
    const modeButtons = [...root.querySelectorAll("[data-mode]")];
    const panels = {
      ask:root.querySelector("#desk-ask-panel"),
      search:root.querySelector("#desk-search-panel")
    };

    function showResult(mode, query, topic) {
      const isSearch = mode === "search";
      const asksTemplate = /공공|표준\s*양식/.test(query);
      const asksBenchmark = /외부\s*사례|벤치마크/.test(query);
      const externalQuery = asksTemplate || asksBenchmark;
      const match = topic ? examples[topic] : (!isSearch && externalQuery ? undefined : Object.values(examples).find(example =>
        (!isSearch || !ownDepartment.checked || example.owned) &&
        example.keywords.some(word => query.includes(word))
      ));
      const response = {
        sources: match ? [match] : (isSearch ? [] : [sourceCards.template,...(includeBenchmark.checked ? [sourceCards.benchmark] : [])]
          .filter(source => externalQuery
            ? (source === sourceCards.template ? asksTemplate : asksBenchmark)
            : sourceCards.matches(source,query))),
        notices:sourceCards.notices.filter(notice => sourceCards.matches(notice,query))
      };
      const external = match ? [] : response.sources;
      if (external.length) {
        const material = sourceCards.kind(external[0]) === "template" ? "외부 공공 표준 양식" : "타사 참고 사례";
        response.header = "안내: 사내 공식 규정에 직접 일치하는 조항이 없어, " + material + "을 기준으로 안내합니다.";
      }
      sourceCards.renderHeader(banner,response.header,response.sources);
      sourceCards.renderSources(externalSources,external);
      sourceCards.renderNotices(qaNotices,isSearch ? [] : response.notices);
      sourceCards.renderConflicts(qaConflicts,isSearch ? [] : response.sources);
      title.textContent = isSearch ? "문서 검색 결과" : "AI 답변";
      caption.textContent = query;
      evidence.hidden = !match;
      evidence.open = false;
      fullSource.hidden = match !== examples.expense && match !== examples.purchase;
      if (!match) {
        copy.textContent = external[0]?.excerpt ?? "질문에 답할 수 있는 문서를 찾지 못했어요. 질문을 바꿔 보세요.";
        return;
      }
      evidence.dataset.source = sourceCards.kind(match);
      root.querySelector("#desk-source-badge").innerHTML = sourceCards.badge(match);
      if (!fullSource.hidden) {
        fullSource.dataset.docId = match === examples.expense ? "expense" : "purchase";
        fullSource.textContent = (match === examples.expense ? "출장비 규정" : "구매 업무 세칙") + " 상세 열기";
      }
      copy.textContent = isSearch ? match.document : match.answer;
      sourceTitle.textContent = match.document;
      sourceExcerpt.textContent = match.excerpt;
    }

    for (const button of modeButtons) {
      button.addEventListener("click", () => {
        const selected = button.dataset.mode;
        for (const modeButton of modeButtons) {
          const active = modeButton.dataset.mode === selected;
          modeButton.setAttribute("aria-pressed", String(active));
          panels[modeButton.dataset.mode].hidden = !active;
        }
        if (selected === "search" && keyword.value.trim()) {
          showResult("search", keyword.value.trim());
        } else if (selected === "search") {
          showResult("search",ownDepartment.checked ? "내 부서" : "열람 가능 전체");
          copy.textContent = "문서 이름이나 업무 용어를 입력해 보세요.";
        } else {
          const value = question.value.trim();
          showResult("ask", value || examples.expense.question, value ? undefined : "expense");
        }
      });
    }
    for (const chip of root.querySelectorAll("[data-topic]")) {
      chip.addEventListener("click", () => {
        const topic = chip.dataset.topic;
        question.value = chip.dataset.question || examples[topic].question;
        showResult("ask", question.value, topic);
      });
    }
    for (const form of root.querySelectorAll("[data-form]")) {
      form.addEventListener("submit", event => {
        event.preventDefault();
        const mode = form.dataset.form;
        const input = mode === "ask" ? question : keyword;
        const query = input.value.trim();
        if (query) showResult(mode, query);
      });
    }
    ownDepartment.addEventListener("change", () => {
      const query = keyword.value.trim();
      if (query) showResult("search", query);
      else caption.textContent = ownDepartment.checked ? "내 부서" : "열람 가능 전체";
    });
    includeBenchmark.addEventListener("change", () => {
      const value = question.value.trim();
      if (value) showResult("ask",value);
    });
    showResult("ask",examples.expense.question,"expense");
  })();

(() => {
    const root = document.getElementById("qd-docs");
    const get = selector => root.querySelector(selector);

    const documents = {
      expense: {
        source_tier:1, doc_type:"official_policy",
        title:"출장비 규정", status:"현행", meta:"사내 규정 · 경영운영팀 · 시행 2026.11.16",
        keywords:"출장비 규정 정산 비용 지출 영수증 증빙 경비", own:true,
        priorTitle:"제3조 지급 대상", prior:"회사의 승인을 받아 수행한 출장에 적용한다.",
        clause:"제4조 비용 정산", excerpt:"정산 신청 시 지출 영수증을 첨부한다.",
        extra:"지출 내역은 출장비 정산 신청서에 기재한다.",
        nextTitle:"제5조 지급 절차", next:"담당 부서는 첨부 증빙과 신청 내역을 검토한다."
      },
      guide: {
        source_tier:1, doc_type:"ojt_guide",
        title:"출장비 정산 업무 가이드", status:"", meta:"업무 가이드 · 재무팀 · 시행 2026.11.12",
        keywords:"출장비 정산 업무 가이드 결재 신청 증빙 절차", own:false,
        priorTitle:"신청서 작성", prior:"출장 목적과 기간, 지출 내역을 입력한다.",
        clause:"증빙 첨부", excerpt:"지출 건별 영수증을 첨부한 뒤 신청서를 제출한다.",
        extra:"첨부 자료와 신청서의 금액이 일치하는지 확인한다.",
        nextTitle:"결재 요청", next:"지정된 결재선에 따라 담당자에게 신청서를 올린다."
      },
      form: {
        ...sourceCards.template,
        status:"", meta:"공공 표준 양식 · 참고용",
        keywords:"출장비 정산 신청서 양식 제출 지출 증빙", own:true,
        priorTitle:"출장 정보", prior:"출장자, 부서, 출장 목적과 기간을 기재한다.",
        clause:"지출 및 첨부 내역", excerpt:"항목별 지출 내역과 첨부한 증빙을 기재한다.",
        extra:"동일 항목의 지출이 여러 건이면 건별로 나누어 작성한다.",
        nextTitle:"신청 확인", next:"신청자가 작성한 내역을 확인한 뒤 제출한다."
      },
      purchase: {
        source_tier:1, doc_type:"official_policy",
        title:"구매 업무 세칙", status:"현행", meta:"사내 규정 · 경영운영팀 · 시행 2026.11.16",
        keywords:"구매 업무 세칙 한도 구매한도", own:true,
        priorTitle:"제2조 적용 범위", prior:"부서의 업무상 구매에 적용한다.",
        clause:"제3조 구매 한도", excerpt:"구매 업무의 적용 한도는 부서 세칙 기준을 따른다.",
        extra:"한도 초과 시 사전 승인을 받는다.",
        nextTitle:"제4조 증빙", next:"구매 승인 내역과 증빙을 보관한다.",
        ontology_graph_meta:sourceCards.ontology_graph_meta
      }
    };
    const previousExpense = {
      ...documents.expense,
      status:"이전 버전 — 현행 아님",
      meta:"사내 규정 · 경영운영팀 · 시행 2026.10.01",
      excerpt:"정산 신청 시 지출 영수증 사본을 제출한다.",
      extra:"출장 결과와 지출 내역을 함께 기록한다."
    };
    const searchView = get("#qd-search-view");
    const documentView = get("#qd-document-view");
    const query = get("#qd-query");
    const own = get("#qd-own");
    const includeBenchmark = get("#qd-benchmark");
    const resultRows = [...root.querySelectorAll("[data-doc-row]")];
    const relatedButtons = [...root.querySelectorAll("[data-related]")];
    const citation = get("#qd-cite-section");
    const version = get("#qd-version");
    const citationPanel = get("#qd-citation-panel");
    const title = get("#qd-doc-title");
    const detailFields = {
      title, meta:get("#qd-doc-meta"), priorTitle:get("#qd-prior-title"), prior:get("#qd-prior-text"),
      clause:get("#qd-clause-title"), excerpt:get("#qd-excerpt"), extra:get("#qd-clause-extra"),
      nextTitle:get("#qd-next-title"), next:get("#qd-next-text")
    };
    let returnFocus;
    for (const row of resultRows) {
      const source = documents[row.dataset.docRow];
      row.querySelector(".qd-result").dataset.source = sourceCards.kind(source);
      row.querySelector(".qd-result-name").insertAdjacentHTML("beforebegin",sourceCards.badge(source));
    }
    function renderResults() {
      const response = {
        results:[],
        reference_templates:!own.checked && sourceCards.matches(sourceCards.template,query.value) ? [sourceCards.template] : [],
        benchmarks:!own.checked && includeBenchmark.checked && sourceCards.matches(sourceCards.benchmark,query.value) ? [sourceCards.benchmark] : [],
        notices:sourceCards.notices.filter(notice => sourceCards.matches(notice,query.value))
      };
      for (const row of resultRows) {
        const doc = documents[row.dataset.docRow];
        const visible = (!own.checked || doc.own) && sourceCards.matches(doc,query.value);
        row.hidden = !visible;
        if (visible) response.results.push(doc);
      }
      const total = response.results.length + response.benchmarks.length;
      const scope = own.checked ? "내 부서" : "열람 가능 전체";
      get("#qd-count").textContent = scope + " · " + total + "건";
      get(".qd-empty").hidden = total > 0 || response.reference_templates.length > 0;
      sourceCards.renderSources(get("#qd-reference-templates"),response.reference_templates);
      sourceCards.renderSources(get("#qd-benchmark-results"),response.benchmarks);
      sourceCards.renderNotices(get("#qd-source-notices"),response.notices);
      sourceCards.renderConflicts(get("#qd-source-conflicts"),response.results);
    }
    function renderDocument(doc, highlight) {
      for (const [field, element] of Object.entries(detailFields)) element.textContent = doc[field];
      const status = get("#qd-doc-status");
      status.textContent = doc.status;
      status.hidden = !doc.status;
      get("#qd-jump-name").textContent = doc.clause;
      citation.classList.toggle("qd-uncited",!highlight);
      citationPanel.hidden = !highlight;
    }
    function showDocument(id, focus = true, notify = true) {
      version.value = "current";
      get("#qd-version-picker").hidden = id !== "expense";
      renderDocument(documents[id],true);
      for (const button of relatedButtons) button.hidden = button.dataset.related === id;
      searchView.hidden = true;
      documentView.hidden = false;
      if (focus) title.focus();
      if (notify) root.dispatchEvent(new CustomEvent("qd-route",{detail:{route:"document",id}}));
    }
    version.addEventListener("change", () => {
      const current = version.value === "current";
      renderDocument(current ? documents.expense : previousExpense,current);
    });
    get(".qd-search-form").addEventListener("submit", event => {
      event.preventDefault();
      renderResults();
    });
    own.addEventListener("change", renderResults);
    includeBenchmark.addEventListener("change", renderResults);
    for (const button of root.querySelectorAll("[data-doc]")) {
      button.addEventListener("click", () => {
        returnFocus = button;
        showDocument(button.dataset.doc);
      });
    }
    for (const button of relatedButtons) {
      button.addEventListener("click", () => showDocument(button.dataset.related));
    }
    function showSearch(focus = true) {
      documentView.hidden = true;
      searchView.hidden = false;
      renderResults();
      if (focus) (returnFocus && !returnFocus.closest("li").hidden ? returnFocus : get("#qd-search-heading")).focus();
    }
    get(".qd-back").addEventListener("click", () => {
      showSearch();
      root.dispatchEvent(new CustomEvent("qd-route",{detail:{route:"search"}}));
    });
    root.addEventListener("qd-show", event => {
      const {route,id,focus} = event.detail;
      if (route === "document") showDocument(id,focus,false);
      else showSearch(focus);
    });
    get(".qd-jump").addEventListener("click", () => citation.focus());
    renderResults();
  })();

"use strict";
    const get = selector => document.querySelector(selector);
    const all = selector => [...document.querySelectorAll(selector)];
    const escapeText = value => String(value ?? "").replace(/[&<>"']/g, character =>
      ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" })[character]
    );
    const navGroups = {
      staff: {label:"직원", links:[["home","AI 질문"],["search","문서 검색"],["document","원문 상세"],["application","가입 신청·상태"]]},
      applicant: {label:"신청자", links:[["application","가입 신청·상태"]]},
      admin: {label:"관리자", links:[["accounts","계정·권한"],["review","문서 검수"],["builder","사규 초안"],["feedback","피드백"],["settings","운영 설정"]]}
    };
    const navRoot = get("#preview-navigation");
    const routes = new Set(Object.values(navGroups).flatMap(group => group.links.map(([route]) => route)));
    const groupKeys = role => role === "admin" ? ["staff","admin"] : [role];
    const makeLinks = links => links.map(([route,label]) =>
      '<a class="nav-link" href="#' + route + '" data-route="' + route + '">' + label + '</a>'
    ).join("");
    function renderNavigation(role) {
      const groups = groupKeys(role).map(key => {
        const group = navGroups[key];
        const label = key === "staff" ? sessionName + "님" : group.label;
        const links = makeLinks(group.links);
        return '<div class="nav-group" role="group" aria-label="' + group.label + ' 메뉴">' +
          '<span class="nav-role-label">' + escapeText(label) + '</span><div class="nav-role-section">' +
          '<div class="nav-inline nav-role-links">' + links + '</div>' +
          '<details class="nav-menu nav-compact"><summary class="nav-trigger" aria-label="' + group.label + ' 화면 메뉴">메뉴</summary>' +
          '<div class="nav-popover">' + links + '</div></details></div></div>';
      });
      navRoot.innerHTML = '<div class="nav-variant' + (role === "admin" ? " nav-dual" : "") +
        '" data-nav-layout="role">' + groups.join("") + '</div>';
    }
    const screens = all("[data-screen]");
    const docsRoot = get("#qd-docs");
    document.addEventListener("click",event => {
      for (const menu of navRoot.querySelectorAll(".nav-menu[open]")) {
        if (!menu.contains(event.target)) menu.open = false;
      }
    });
    document.addEventListener("keydown",event => {
      if (event.key !== "Escape") return;
      const open = navRoot.querySelector(".nav-menu[open]");
      if (open) {
        open.open = false;
        open.querySelector("summary").focus();
      }
    });
    const departmentNames = ["경영운영팀","재무팀","개발팀"];
    const departments = new Map(departmentNames.map(dept => [dept,true]));
    const roles = [["EMPLOYEE","직원"],["NEW_HIRE","신규 입사자"],["DEPT_MANAGER","부서장"],["ADMIN","관리자"]];
    const grades = ["사원","주임","대리","과장","차장","부장"];
    const accounts = [
      {id:"sample",name:"김지민",email:"sample@example.com",requested:"경영운영팀",department:"경영운영팀",role:"NEW_HIRE",status:"승인 대기",employeeId:""},
      {id:"active",name:"박서윤",email:"staff@example.com",requested:"재무팀",department:"재무팀",grade:"대리",role:"EMPLOYEE",status:"사용 중",employeeId:"EMP-002",firstLogin:true}
    ];
    const reviews = [
      {id:"pipeline",title:"출장비 규정 개정",reason:"자동 분류 신뢰도 낮음",department:"경영운영팀",access:"전사",date:"2026-12-01",before:"지출 영수증을 제출한다.",after:"정산 신청 시 지출 영수증을 건별로 첨부한다.",status:"검수 대기"}
    ];
    const feedback = [
      {id:"fb-expense",type:"오답 신고",title:"출장비 정산 기한이 달라요",body:"AI 답변과 현행 출장비 규정의 정산 기한이 달라요.",source:"출장비 규정 · 제4조",status:"처리 대기"},
      {id:"fb-form",type:"개정 요청",title:"정산 신청 양식을 갱신해 주세요",body:"연결된 신청서를 현행 양식으로 바꿔 주세요.",source:"출장비 정산 신청서",status:"처리 대기"}
    ];
    const templates = {
      expense:{title:"출장비 규정",rule:"출장 종료 후 30일 이내 정산 신청",subject:"업무상 출장과 관련한 비용 정산"},
      leave:{title:"휴가 운영 규정",rule:"휴가 시작 3영업일 전 신청",subject:"임직원의 휴가 신청과 승인"},
      approval:{title:"위임전결 규정",rule:"업무별 결재 기준표에 따라 승인 요청",subject:"사내 업무의 결재 권한과 절차"}
    };
    let selectedAccount = "sample";
    let selectedReview = "pipeline";
    let selectedFeedback = "fb-expense";
    let feedbackFilter = "open";
    let firstLoginAccount;
    let sessionRole;
    let sessionEmail;
    let sessionName;
    let draft = null;
    let currentPack = "IT 서비스 팩";
    let docId = "expense";
    let ojtStatus = "";

    const badge = value => {
      const tone = /사용 중|승인|처리 완료/.test(value) && !/대기/.test(value) ? "good" :
        /차단|거절|반려|종결/.test(value) ? "bad" : "warn";
      return '<span class="badge ' + tone + '">' + escapeText(value) + '</span>';
    };
    const options = values => values.map(value => '<option>' + escapeText(value) + '</option>').join("");
    const snapshot = entries => '<dl class="snapshot">' + entries.map(([label,value]) =>
      '<div><dt>' + escapeText(label) + '</dt><dd>' + escapeText(value) + '</dd></div>').join("") + '</dl>';
    const item = (kind,id,title,status,meta,selected) =>
      '<button type="button" class="item" data-pick="' + kind + '" data-id="' + id + '" aria-pressed="' + (id === selected) +
      '"><strong>' + escapeText(title) + '</strong>' + badge(status) + '<span class="small">' + escapeText(meta) + '</span></button>';
    const button = (name,label,classes = "") => '<button class="btn ' + classes + '" type="submit" name="action" value="' + name + '">' + label + '</button>';
    const note = (selector,message) => { get(selector).textContent = message; };
    const canOpen = route => sessionRole && groupKeys(sessionRole).some(key =>
      navGroups[key].links.some(([name]) => name === route)
    );
    const applicationAccount = () => accounts.find(record => record.email.toLowerCase() === sessionEmail);

    function setNavigation(route) {
      navRoot.querySelectorAll("[data-route]").forEach(link => {
        if (link.dataset.route === route) link.setAttribute("aria-current","page");
        else link.removeAttribute("aria-current");
      });
      for (const menu of navRoot.querySelectorAll(".nav-menu")) {
        menu.open = false;
        menu.toggleAttribute("data-current",Boolean(menu.querySelector('[aria-current="page"]')));
      }
    }
    function show(route, focus = true) {
      const section = route === "search" || route === "document" ? "docs" : route;
      for (const screen of screens) screen.hidden = screen.dataset.screen !== section;
      navRoot.hidden = !sessionRole;
      get("#logout").hidden = !sessionRole;
      get(".masthead .dept").hidden = !sessionRole || sessionRole === "applicant";
      setNavigation(route);
      if (route === "search" || route === "document") {
        docsRoot.dispatchEvent(new CustomEvent("qd-show", {detail:{route,id:docId,focus}}));
      } else {
        const heading = get('[data-screen="' + section + '"] h1');
        if (focus && heading) {
          heading.tabIndex = -1;
          heading.focus();
        }
      }
    }
    function cleanHomeUrl() {
      return location.href.split("#")[0];
    }
    function navigate(route, focus = true) {
      if (!canOpen(route)) return;
      const hash = route === "home" ? "" : "#" + route;
      if (location.hash !== hash) history.pushState(null,"",hash || cleanHomeUrl());
      show(route,focus);
    }
    function restoreRoute(focus = true) {
      const requested = location.hash.slice(1);
      let route = "auth";
      if (sessionRole) {
        route = canOpen(requested) ? requested : navGroups[sessionRole].links[0][0];
      }
      const hash = route === "auth" || route === "home" ? "" : "#" + route;
      if (location.hash !== hash) history.replaceState(null,"",hash || cleanHomeUrl());
      show(route,focus);
    }
    window.addEventListener("popstate", () => restoreRoute());
    window.addEventListener("hashchange", () => restoreRoute());
    document.addEventListener("click",event => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      const link = event.target.closest('a[href^="#"]');
      if (!link || !routes.has(link.hash.slice(1))) return;
      event.preventDefault();
      if (link.dataset.docId) docId = link.dataset.docId;
      navigate(link.hash.slice(1));
    });
    docsRoot.addEventListener("qd-route", event => {
      const {route,id} = event.detail;
      if (id) docId = id;
      if (!canOpen(route)) return;
      if (location.hash !== "#" + route) history.pushState(null,"","#" + route);
      setNavigation(route);
    });
    get('#question-desk-home [data-form="search"]').addEventListener("submit", () => {
      const keyword = get("#desk-keyword").value.trim();
      if (keyword) {
        get("#qd-query").value = keyword;
        get("#qd-own").checked = get("#desk-own").checked;
        get("#qd-benchmark").checked = get("#desk-search-benchmark").checked;
        navigate("search");
      }
    });

    function activeDepartmentOptions() {
      return [...departments].filter(([,active]) => active).map(([department]) => department);
    }
    function showApplicationMode(mode) {
      all("[data-application-mode]").forEach(control => control.setAttribute("aria-pressed",String(control.dataset.applicationMode === mode)));
      all("[data-application-panel]").forEach(panel => { panel.hidden = panel.dataset.applicationPanel !== mode; });
      if (mode === "status") renderApplication();
    }
    function renderApplication() {
      const account = applicationAccount();
      if (!account) {
        note("#application-status","제출한 가입 신청이 없습니다.");
        return;
      }
      const statusDetails = {
        "승인 대기": '<p class="state-note">부서 관리자가 신청 내용을 검토 중입니다.</p><details class="auth-preview"><summary>공개 문서 보기</summary><p><strong>신입 업무 안내</strong></p><p>입사 첫날에는 사내 계정 발급 상태를 확인하고 부서 담당자에게 온보딩 일정을 문의해 주세요.</p></details>',
        "보완 요청": '<p class="state-note"><strong>보완 사유</strong> ' + escapeText(account.revisionReason || "신청 정보와 희망 부서를 다시 확인해 주세요.") + '</p><div><button type="button" class="btn btn-wide" id="amend-application">신청 정보 수정·재제출</button></div>',
        "거절": '<p class="state-note">가입 신청이 거절되었습니다. 소속 부서 관리자 또는 사내 계정 담당자에게 문의해 주세요.</p>',
        "사용 중": '<p class="state-note">가입이 승인되었습니다.</p>',
        "차단": '<p class="state-note">계정이 차단되었습니다. 사내 계정 담당자에게 문의해 주세요.</p>'
      };
      get("#application-status").innerHTML = '<div class="row row-between"><h2>가입 신청 상태</h2>' + badge(account.status) + '</div>' +
        snapshot([["신청자",account.name],["희망 부서",account.requested]]) +
        (statusDetails[account.status] || '');
    }
    function renderSignupDepartments() {
      const select = get('#signup-form [name="department"]');
      const current = select.value;
      select.innerHTML = options(activeDepartmentOptions());
      if (departments.get(current)) select.value = current;
    }
    all("[data-application-mode]").forEach(control => control.addEventListener("click", () => {
      showApplicationMode(control.dataset.applicationMode);
      note("#application-notice","");
    }));
    const firstLoginNotice = get("#first-login-notice");
    const loginEmail = get("#login-email");
    function openSession(role) {
      sessionRole = role;
      const landing = navGroups[sessionRole].links[0][0];
      get(".brand").href = "#" + landing;
      renderNavigation(sessionRole);
      navigate(landing);
    }
    function finishLogin(email,role) {
      sessionEmail = email;
      const account = applicationAccount();
      sessionName = account?.name ?? "이다온";
      const form = get("#signup-form");
      form.reset();
      form.elements.email.value = email;
      renderSignupDepartments();
      if (account) {
        form.elements.displayName.value = account.name;
        form.elements.employeeId.value = account.employeeId;
        if (departments.get(account.requested)) form.elements.department.value = account.requested;
      }
      note("#application-notice","");
      showApplicationMode(account ? "status" : "signup");
      openSession(role);
    }
    get("#logout").addEventListener("click",() => {
      sessionRole = undefined;
      sessionEmail = undefined;
      sessionName = undefined;
      navRoot.replaceChildren();
      note("#application-status","");
      note("#auth-notice","");
      get(".brand").href = "#home";
      history.replaceState(null,"",cleanHomeUrl());
      show("auth");
    });
    loginEmail.addEventListener("input",() => {
      firstLoginAccount = undefined;
      firstLoginNotice.hidden = true;
    });
    get("#login-form").addEventListener("submit",event => {
      event.preventDefault();
      const enteredEmail = loginEmail.value.trim().toLowerCase();
      const email = enteredEmail === "demo@example.com" ? "employee@example.com" : enteredEmail;
      if (event.currentTarget.elements.password.value !== "demo-password") {
        firstLoginAccount = undefined;
        firstLoginNotice.hidden = true;
        note("#auth-notice","비밀번호를 확인해 주세요.");
        return;
      }
      const account = accounts.find(record => record.email.toLowerCase() === email);
      if (account?.status === "차단") {
        note("#auth-notice","계정이 차단되었습니다. 사내 계정 담당자에게 문의해 주세요.");
        return;
      }
      if (!account && !["employee@example.com","admin@example.com","newhire@example.com"].includes(email)) {
        note("#auth-notice","입력한 이메일을 확인해 주세요.");
        return;
      }
      if (account?.firstLogin) {
        firstLoginAccount = account;
        firstLoginNotice.hidden = false;
        note("#auth-notice","");
        get("#first-login-privacy-link").focus();
        return;
      }
      const role = account ? (account.status !== "사용 중" ? "applicant" : account.role === "ADMIN" ? "admin" : "staff")
        : ({ "admin@example.com":"admin", "employee@example.com":"staff", "newhire@example.com":"applicant" })[email];
      finishLogin(email,role);
    });
    get("#first-login-privacy-link").addEventListener("click",event => {
      event.preventDefault();
      const preview = get("#first-login-privacy");
      preview.open = true;
      preview.querySelector("summary").focus();
    });
    get("#first-login-continue").addEventListener("click",() => {
      if (!firstLoginAccount || firstLoginAccount.status !== "사용 중") return;
      const {email,role} = firstLoginAccount;
      firstLoginAccount.firstLogin = false;
      firstLoginAccount = undefined;
      firstLoginNotice.hidden = true;
      finishLogin(email.toLowerCase(),role === "ADMIN" ? "admin" : "staff");
    });
    get("#signup-form").addEventListener("submit",event => {
      event.preventDefault();
      if (!canOpen("application")) return;
      if (sessionRole === "admin") {
        note("#application-notice","관리자 계정은 가입 신청 없이 이용할 수 있습니다.");
        return;
      }
      const input = new FormData(event.currentTarget);
      const name = String(input.get("displayName")).trim();
      if (!name || !departments.get(input.get("department"))) {
        note("#application-notice","이름과 사용 가능한 희망 부서를 입력해 주세요.");
        return;
      }
      let account = applicationAccount();
      if (!account) {
        account = {id:"signup-" + accounts.length,email:sessionEmail,role:"NEW_HIRE",status:"가입 신청"};
        accounts.unshift(account);
      } else if (!["가입 신청","보완 요청"].includes(account.status)) {
        showApplicationMode("status");
        note("#application-notice","현재 계정의 신청 상태를 표시했습니다.");
        return;
      }
      Object.assign(account,{name,requested:input.get("department"),department:input.get("department"),employeeId:String(input.get("employeeId")).trim(),status:"승인 대기"});
      selectedAccount = account.id;
      showApplicationMode("status");
      openSession("applicant");
      note("#application-notice","가입 신청을 제출했습니다.");
      renderAccounts();
    });
    get("#application-status").addEventListener("click",event => {
      if (event.target.id !== "amend-application") return;
      showApplicationMode("signup");
      get('#signup-form [name="displayName"]').focus();
    });

    function renderAccounts(message = "") {
      const account = accounts.find(record => record.id === selectedAccount);
      note("#account-count","승인 대기 " + accounts.filter(record => record.status === "승인 대기").length + "건");
      get("#account-list").innerHTML = accounts.map(record => item("account",record.id,record.name,record.status,record.requested,selectedAccount)).join("");
      const isActive = account.status === "사용 중" || account.status === "차단";
      const canReview = account.status === "승인 대기";
      const availableDepartments = departmentNames.filter(name => departments.get(name) || account.department === name);
      get("#account-detail").innerHTML = '<div class="row row-between"><h2>' + escapeText(account.name) + '</h2>' + badge(account.status) + '</div>' +
        snapshot([["사내 이메일",account.email],["희망 부서",account.requested],["사번",account.employeeId || "미입력"]]) +
        '<form class="form" data-account-action><label>부서 <select name="department">' + options(availableDepartments) + '</select></label>' +
        '<label>직급 <select name="grade">' + options(grades) + '</select></label>' +
        '<details class="account-access"><summary>시스템 접근 권한</summary><label>접근 권한 <select name="role">' +
        roles.map(([value,label]) => '<option value="' + value + '">' + label + '</option>').join("") + '</select></label></details>' +
        (canReview ? '<label>보완 요청 사유 <input name="revisionReason" maxlength="160" placeholder="보완 요청 시 입력"></label>' : '') +
        '<div class="row">' +
        (canReview ? button("approve","승인","btn-primary") + button("amend","보완 요청") + button("reject","거절","btn-danger") :
          isActive ? button("save","권한 저장","btn-primary") + (account.status === "차단" ? button("enable","차단 해제") : button("block","계정 차단","btn-danger")) : '') +
        '</div></form><p class="notice" aria-live="polite">' + escapeText(message) + '</p>';
      const form = get("[data-account-action]");
      form.elements.department.value = account.department;
      form.elements.grade.value = account.grade || grades[0];
      form.elements.role.value = account.role;
      if (!canReview && !isActive) {
        form.elements.department.disabled = true;
        form.elements.grade.disabled = true;
        form.elements.role.disabled = true;
      }
    }
    get("#account-detail").addEventListener("submit",event => {
      event.preventDefault();
      const account = accounts.find(record => record.id === selectedAccount);
      const action = event.submitter?.value;
      const status = {approve:"사용 중",amend:"보완 요청",reject:"거절",block:"차단",enable:"사용 중"}[action];
      if (action === "approve" || action === "save") {
        account.department = event.target.elements.department.value;
        account.grade = event.target.elements.grade.value;
        account.role = event.target.elements.role.value;
      }
      if (action === "amend") account.revisionReason = event.target.elements.revisionReason.value.trim();
      if (status) account.status = status;
      if (status || action === "save") {
        renderAccounts(action === "save" ? "계정 정보를 저장했습니다." : "계정 상태를 ‘" + status + "’으로 변경했습니다.");
      }
    });

    function renderReview(message = "") {
      const pending = reviews.filter(record => record.status === "검수 대기");
      const record = reviews.find(entry => entry.id === selectedReview);
      note("#review-count",pending.length + "건");
      get("#review-list").innerHTML = pending.map(doc => item("review",doc.id,doc.title,doc.status,doc.reason + " · " + doc.date,selectedReview)).join("") || '<p class="small">검수 대기 문서가 없습니다.</p>';
      const detail = get("#review-detail");
      if (!record) {
        detail.innerHTML = '<p>검수할 문서를 선택하세요.</p>';
        return;
      }
      detail.innerHTML = '<div class="row row-between"><h2>' + escapeText(record.title) + '</h2>' + badge(record.status) + '</div>' +
        snapshot([["검수 사유",record.reason],["시행일",record.date],["추천 주관 부서",record.department],["열람 범위",record.access]]) +
        '<div class="compare"><div><h3>현행본</h3><p>' + escapeText(record.before || "신규 문서") + '</p></div><div><h3>검수 대상</h3><p>' + escapeText(record.after) + '</p></div></div>' +
        (record.status === "검수 대기" ? '<form class="row" data-review-action>' + button("publish","승인·발행","btn-primary") + button("reject","반려","btn-danger") +
          (record.id === draft?.id ? '<button class="btn" type="button" data-open-builder>사규 초안에서 열기</button>' : '') + '</form>' : '') +
        '<p class="notice" aria-live="polite">' + escapeText(message) + '</p>';
    }
    get("#review-detail").addEventListener("submit",event => {
      event.preventDefault();
      const record = reviews.find(entry => entry.id === selectedReview);
      const action = event.submitter?.value;
      if (!record || record.status !== "검수 대기" || !["publish","reject"].includes(action)) return;
      record.status = action === "publish" ? "승인·발행" : "반려";
      renderReview("문서를 ‘" + record.status + "’ 상태로 변경했습니다.");
      if (record === draft) note("#builder-notice", action === "publish" ? "초안이 검수에서 승인되었습니다." : "초안이 반려되었습니다. 본문을 수정해 다시 제출할 수 있습니다.");
      renderDraft();
    });
    get("#review-detail").addEventListener("click",event => {
      if (event.target.closest("[data-open-builder]")) navigate("builder");
    });

    function fillTemplate() {
      const template = templates[get("#builder-template").value];
      get("#builder-title").value = template.title;
      get("#builder-rule").value = template.rule;
    }
    function renderDraft() {
      const status = draft?.status || "작성 중";
      const locked = status === "검수 대기" || status === "승인·발행";
      get("#builder-status").outerHTML = badge(status).replace("<span","<span id=\"builder-status\"");
      get("#draft-body").readOnly = locked;
      all("#builder-form input, #builder-form select, #builder-form button").forEach(control => { control.disabled = locked; });
      get("#draft-submit").disabled = locked;
      get("#draft-review").hidden = !draft;
      get("#draft-new").hidden = status !== "승인·발행";
    }
    get("#builder-template").addEventListener("change",fillTemplate);
    get("#builder-form").addEventListener("submit",event => {
      event.preventDefault();
      const template = templates[get("#builder-template").value];
      const rule = get("#builder-rule").value.trim();
      get("#draft-body").value = "제1조 목적\n이 규정은 " + template.subject + "에 필요한 기준과 절차를 정한다.\n\n제2조 적용 기준\n" + rule + ".\n\n부칙\n이 규정은 " + get("#builder-date").value + "부터 시행한다.";
      note("#builder-notice","초안을 생성했습니다. 제출 전에 문구를 확인해 주세요.");
    });
    get("#draft-submit").addEventListener("click",() => {
      const body = get("#draft-body").value.trim();
      const title = get("#builder-title").value.trim();
      const date = get("#builder-date").value;
      if (!body || !title || !date) {
        note("#builder-notice","문서 제목, 시행일, 초안 본문을 입력해 주세요.");
        return;
      }
      if (!draft) {
        draft = {id:"draft-" + reviews.length,before:"",department:"경영운영팀",access:"전사",reason:"사규 초안 제출"};
        reviews.unshift(draft);
      }
      Object.assign(draft,{title,date,after:body,status:"검수 대기"});
      selectedReview = draft.id;
      note("#builder-notice","문서 검수 목록에 초안을 제출했습니다.");
      renderReview();
      renderDraft();
    });
    get("#draft-review").addEventListener("click",() => {
      if (draft) selectedReview = draft.id;
      renderReview();
      navigate("review");
    });
    get("#draft-new").addEventListener("click",() => {
      draft = null;
      get("#draft-body").value = "";
      fillTemplate();
      note("#builder-notice","");
      renderDraft();
    });

    function renderFeedback(message = "") {
      const visible = feedback.filter(record => feedbackFilter === "all" || ["처리 대기","검토 중"].includes(record.status));
      const record = feedback.find(entry => entry.id === selectedFeedback);
      all("[data-feedback-filter]").forEach(control => control.setAttribute("aria-pressed",String(control.dataset.feedbackFilter === feedbackFilter)));
      get("#feedback-list").innerHTML = visible.map(entry => item("feedback",entry.id,entry.title,entry.status,entry.type,selectedFeedback)).join("") || '<p class="small">처리 대기 피드백이 없습니다.</p>';
      get("#feedback-detail").innerHTML = record ? '<div class="row row-between"><h2>' + escapeText(record.title) + '</h2>' + badge(record.status) + '</div>' +
        snapshot([["유형",record.type],["관련 문서",record.source]]) + '<p class="state-note">' + escapeText(record.body) + '</p>' +
        (["처리 대기","검토 중"].includes(record.status) ? '<form class="row" data-feedback-action>' +
          (record.status === "처리 대기" ? button("start","검토 시작","btn-primary") : '') + button("resolve","처리 완료") + button("dismiss","종결") + '</form>' : '') +
        '<p class="notice" aria-live="polite">' + escapeText(message) + '</p>' : '<p>피드백을 선택하세요.</p>';
    }
    all("[data-feedback-filter]").forEach(control => control.addEventListener("click",() => {
      feedbackFilter = control.dataset.feedbackFilter;
      if (feedbackFilter === "open" && !feedback.some(record => record.id === selectedFeedback && ["처리 대기","검토 중"].includes(record.status))) {
        selectedFeedback = feedback.find(record => ["처리 대기","검토 중"].includes(record.status))?.id;
      }
      renderFeedback();
    }));
    get("#feedback-detail").addEventListener("submit",event => {
      event.preventDefault();
      const record = feedback.find(entry => entry.id === selectedFeedback);
      const value = {start:"검토 중",resolve:"처리 완료",dismiss:"종결"}[event.submitter?.value];
      if (!record || !value) return;
      record.status = value;
      renderFeedback("피드백을 ‘" + value + "’ 상태로 변경했습니다.");
    });

    document.addEventListener("click",event => {
      const control = event.target.closest("[data-pick]");
      if (!control) return;
      const {pick,id} = control.dataset;
      if (pick === "account") { selectedAccount = id; renderAccounts(); }
      if (pick === "review") { selectedReview = id; renderReview(); }
      if (pick === "feedback") { selectedFeedback = id; renderFeedback(); }
      get('[data-pick="' + pick + '"][data-id="' + id + '"]')?.focus();
    });

    function renderSettings() {
      get("#department-list").innerHTML = [...departments].map(([department,active]) =>
        '<div class="setting-row"><strong>' + escapeText(department) + '</strong><label><input type="checkbox" data-department="' + escapeText(department) +
        '" aria-label="' + escapeText(department) + ' 활성화"' + (active ? ' checked' : '') + '></label></div>').join("");
      note("#current-pack","현재 사용 중 · " + currentPack);
      get("#ojt-actions").hidden = Boolean(ojtStatus);
    }
    get("#department-list").addEventListener("change",event => {
      const department = event.target.dataset.department;
      if (!department) return;
      departments.set(department,event.target.checked);
      renderSettings();
      renderSignupDepartments();
      renderAccounts();
      get('[data-department="' + department + '"]').focus();
      note("#settings-notice",department + "을(를) " + (departments.get(department) ? "활성화" : "비활성화") + "했습니다.");
    });
    get("#ojt-actions").addEventListener("click",event => {
      const action = event.target.dataset.ojt;
      if (!action) return;
      ojtStatus = action;
      if (action === "승인") {
        const chip = get('#question-desk-home [data-topic="expense"]');
        chip.textContent = "첫 출장 정산 서류";
        chip.dataset.question = "첫 출장을 다녀온 뒤 어떤 서류를 내나요?";
      }
      renderSettings();
      note("#settings-notice","추천 질문 변경 제안을 " + action + "했습니다.");
    });
    get("#pack-form").addEventListener("submit",event => {
      event.preventDefault();
      currentPack = get("#pack-select").value;
      renderSettings();
      note("#settings-notice","현재 지식 패키지를 ‘" + currentPack + "’으로 설정했습니다.");
    });

    renderAccounts();
    renderReview();
    fillTemplate();
    renderDraft();
    renderFeedback();
    renderSettings();
    restoreRoute(false);
