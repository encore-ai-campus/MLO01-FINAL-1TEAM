"use strict";

document.addEventListener("htmx:beforeSwap", event => {
  if (event.detail.xhr.getResponseHeader("X-Upmutok-Fragment") === "true") {
    event.detail.shouldSwap = true;
  }
});

document.addEventListener("htmx:afterSwap", event => {
  const citation = event.detail.target.querySelector("#ui-citation");
  if (citation) citation.focus();
});

function showRequestError(event) {
  const target = document.querySelector("#ui-result");
  if (!target || event.detail.xhr?.getResponseHeader("X-Upmutok-Fragment") === "true") return;
  const notice = document.createElement("p");
  notice.className = "state-note";
  notice.setAttribute("role", "alert");
  notice.textContent = event.detail.xhr?.status === 413
    ? "입력한 내용이 너무 길어요."
    : "요청을 처리하지 못했어요. 잠시 뒤 다시 시도해 주세요.";
  target.replaceChildren(notice);
}

document.addEventListener("htmx:responseError", showRequestError);
document.addEventListener("htmx:sendError", showRequestError);
document.querySelector("#ui-citation")?.focus();
