import "./style.css";
import html2canvas from "html2canvas";
import { createWorker } from "tesseract.js";
import * as pdfjsLib from "pdfjs-dist/legacy/build/pdf.mjs";
import pdfWorkerUrl from "pdfjs-dist/legacy/build/pdf.worker.mjs?url";

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

const fileInput = document.querySelector("#file-input");
const processButton = document.querySelector("#process-button");
const fileName = document.querySelector("#file-name");
const status = document.querySelector("#status");
const resultSection = document.querySelector("#result-section");
const resultList = document.querySelector("#result-list");
const finalResult = document.querySelector("#final-result");
const resultDate = document.querySelector("#result-date");
const downloadButton = document.querySelector("#download-button");
const certificate = document.querySelector("#certificate");

const supportedTypes = new Set(["image/jpeg", "image/png", "application/pdf"]);

function setStatus(message, kind = "") {
  status.textContent = message;
  status.className = `status ${kind}`;
}

function validateFile(file) {
  if (!file) return "업로드할 파일을 선택하세요.";
  if (!supportedTypes.has(file.type)) return "JPG, PNG, PDF 형식의 파일만 업로드할 수 있습니다.";
  return "";
}

fileInput.addEventListener("change", () => {
  const file = fileInput.files?.[0];
  const error = validateFile(file);
  resultSection.classList.add("hidden");
  processButton.disabled = Boolean(error);
  fileName.textContent = file ? file.name : "선택된 파일이 없습니다.";
  setStatus(error, error ? "error" : "");
});

async function pdfToImages(file) {
  const data = new Uint8Array(await file.arrayBuffer());
  const pdf = await pdfjsLib.getDocument({ data }).promise;
  const pages = [];
  for (let pageNumber = 1; pageNumber <= pdf.numPages; pageNumber += 1) {
    const page = await pdf.getPage(pageNumber);
    const viewport = page.getViewport({ scale: 2 });
    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil(viewport.width);
    canvas.height = Math.ceil(viewport.height);
    await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
    pages.push(canvas);
  }
  return pages;
}

async function recognizeDocument(file) {
  const targets = file.type === "application/pdf" ? await pdfToImages(file) : [file];
  const worker = await createWorker(["kor", "eng"]);
  try {
    let text = "";
    for (let index = 0; index < targets.length; index += 1) {
      setStatus(`문자와 숫자를 인식하는 중입니다. (${index + 1}/${targets.length})`, "working");
      const { data } = await worker.recognize(targets[index]);
      text += `\n${data.text}`;
    }
    return text.trim();
  } finally {
    await worker.terminate();
  }
}

function organizeTransactions(text) {
  return text.split(/\r?\n/).map((line) => line.replace(/\s+/g, " ").trim()).filter(Boolean);
}

function checkTransaction(record) {
  const hasText = /[A-Za-z가-힣]/.test(record);
  const hasNumber = /\d/.test(record);
  return hasText && hasNumber ? "확인" : "확인 불가";
}

function showResults(records) {
  resultList.replaceChildren();
  records.forEach((record) => {
    const row = document.createElement("tr");
    const recordCell = document.createElement("td");
    const resultCell = document.createElement("td");
    const result = checkTransaction(record);
    recordCell.textContent = record;
    resultCell.textContent = result;
    resultCell.className = result === "확인" ? "pass" : "fail";
    row.append(recordCell, resultCell);
    resultList.append(row);
  });
  const allConfirmed = records.length > 0 && records.every((record) => checkTransaction(record) === "확인");
  finalResult.textContent = allConfirmed ? "확인" : "확인 불가";
  finalResult.className = allConfirmed ? "pass" : "fail";
  resultDate.textContent = new Intl.DateTimeFormat("ko-KR").format(new Date());
  resultSection.classList.remove("hidden");
}

processButton.addEventListener("click", async () => {
  const file = fileInput.files?.[0];
  const error = validateFile(file);
  if (error) return setStatus(error, "error");
  processButton.disabled = true;
  resultSection.classList.add("hidden");
  try {
    setStatus("문서를 준비하는 중입니다.", "working");
    const text = await recognizeDocument(file);
    const records = organizeTransactions(text);
    if (!records.length) throw new Error("문서에서 문자 또는 숫자를 인식하지 못했습니다. 파일 상태를 확인한 뒤 다시 시도하세요.");
    showResults(records);
    setStatus("문서 인식과 거래 내역 확인이 완료되었습니다.", "success");
  } catch (error) {
    setStatus(error.message || "처리 중 문제가 발생했습니다. 다시 시도하세요.", "error");
  } finally {
    processButton.disabled = false;
  }
});

downloadButton.addEventListener("click", async () => {
  try {
    setStatus("공문 형태의 결과 이미지를 생성하는 중입니다.", "working");
    const canvas = await html2canvas(certificate, { backgroundColor: "#ffffff", scale: 2 });
    const link = document.createElement("a");
    link.download = "금융거래확인-결과.png";
    link.href = canvas.toDataURL("image/png");
    link.click();
    setStatus("결과 이미지를 다운로드했습니다.", "success");
  } catch {
    setStatus("결과 이미지 생성 또는 다운로드에 실패했습니다. 다시 시도하세요.", "error");
  }
});
