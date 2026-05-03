import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np
from paddleocr import PaddleOCR


@dataclass
class EvalRow:
    file: str
    merchant: str
    total_amount: int
    full_text: str


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            ins = cur[j - 1] + 1
            delete = prev[j] + 1
            replace = prev[j - 1] + (0 if ca == cb else 1)
            cur.append(min(ins, delete, replace))
        prev = cur
    return prev[-1]


def normalize_text(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def normalize_name(s: str) -> str:
    s = normalize_text(s)
    return re.sub(r"[^0-9a-z가-힣]", "", s)


def parse_amount_candidates(text: str) -> List[int]:
    nums = []
    for token in re.findall(r"\d[\d,]{2,}", text):
        try:
            val = int(token.replace(",", ""))
        except ValueError:
            continue
        if 100 <= val <= 5_000_000:
            nums.append(val)
    return nums


def extract_fields_from_text(text: str) -> Tuple[str, int]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    merchant = "알 수 없음"
    for line in lines[:8]:
        low = line.lower()
        if any(k in low for k in ["사업자", "전화", "카드", "승인", "vat", "합계", "total"]):
            continue
        if len(re.findall(r"\d", line)) > max(3, len(line) // 2):
            continue
        merchant = line
        break

    total_keywords = ["합계", "총액", "결제금액", "승인금액", "total", "amount", "결제"]
    negative_keywords = ["할인", "쿠폰", "부가세", "vat", "잔액", "거스름", "면세"]
    ranked: List[Tuple[int, int]] = []
    for idx, line in enumerate(lines):
        low = line.lower()
        near = " ".join(lines[max(0, idx - 1): min(len(lines), idx + 2)]).lower()
        amounts = parse_amount_candidates(near)
        if not amounts:
            continue
        best_amt = max(amounts)
        score = 0
        if any(k in low for k in total_keywords):
            score += 100
        if any(k in low for k in negative_keywords):
            score -= 60
        if score == 0 and any(k in near for k in total_keywords):
            score += 70
        ranked.append((score, best_amt))
    if ranked:
        ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return merchant, ranked[0][1]

    fallback = parse_amount_candidates(text)
    return merchant, (max(fallback) if fallback else 0)


def ocr_image(ocr: PaddleOCR, image_path: Path) -> str:
    img = cv2.imread(str(image_path))
    if img is None:
        return ""
    img = cv2.resize(img, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    thr = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 2)
    result = ocr.ocr(thr)
    lines: List[str] = []
    for block in result:
        for word in block:
            lines.append(word[1][0])
    return "\n".join(lines)


def read_labels(path: Path) -> List[EvalRow]:
    rows: List[EvalRow] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("file"):
                continue
            amt_raw = (row.get("total_amount") or "").replace(",", "").strip()
            try:
                amt = int(amt_raw) if amt_raw else 0
            except ValueError:
                amt = 0
            rows.append(
                EvalRow(
                    file=row["file"].strip(),
                    merchant=(row.get("merchant") or "").strip(),
                    total_amount=amt,
                    full_text=(row.get("full_text") or "").strip(),
                )
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate receipt OCR accuracy")
    parser.add_argument("--images", default="eval/images", help="Directory containing receipt images")
    parser.add_argument("--labels", default="eval/labels.csv", help="CSV labels file")
    args = parser.parse_args()

    images_dir = Path(args.images)
    labels_path = Path(args.labels)
    if not labels_path.exists():
        raise FileNotFoundError(f"labels file not found: {labels_path}")
    rows = read_labels(labels_path)
    if not rows:
        raise RuntimeError("labels.csv is empty")

    ocr = PaddleOCR(lang="korean", use_angle_cls=True, use_gpu=False)

    merchant_hit = 0
    amount_hit = 0
    cer_sum = 0.0
    wer_sum = 0.0
    text_eval_count = 0
    evaluated = 0

    print("file,merchant_ok,amount_ok,pred_merchant,pred_amount")
    for row in rows:
        img_path = images_dir / row.file
        if not img_path.exists():
            print(f"{row.file},False,False,IMAGE_NOT_FOUND,0")
            continue
        pred_text = ocr_image(ocr, img_path)
        pred_merchant, pred_amount = extract_fields_from_text(pred_text)

        m_ok = normalize_name(pred_merchant) == normalize_name(row.merchant) and row.merchant != ""
        a_ok = pred_amount == row.total_amount and row.total_amount > 0
        merchant_hit += int(m_ok)
        amount_hit += int(a_ok)

        gt_text = normalize_text(row.full_text)
        pd_text = normalize_text(pred_text)
        if gt_text:
            cer = levenshtein(pd_text, gt_text) / max(1, len(gt_text))
            gt_words = gt_text.split()
            pd_words = pd_text.split()
            wer = levenshtein(" ".join(pd_words), " ".join(gt_words)) / max(1, len(gt_words))
            cer_sum += cer
            wer_sum += wer
            text_eval_count += 1
        evaluated += 1

        pred_merchant_safe = pred_merchant.replace(",", " ")
        print(f"{row.file},{m_ok},{a_ok},{pred_merchant_safe},{pred_amount}")

    if evaluated == 0:
        raise RuntimeError("No rows evaluated. Check image paths and labels.")

    merchant_acc = merchant_hit / evaluated * 100
    amount_acc = amount_hit / evaluated * 100
    cer_avg = cer_sum / max(1, text_eval_count) * 100
    wer_avg = wer_sum / max(1, text_eval_count) * 100

    print("\n=== Summary ===")
    print(f"Samples: {evaluated}")
    print(f"Merchant Accuracy: {merchant_acc:.2f}%")
    print(f"Total Amount Accuracy: {amount_acc:.2f}%")
    print(f"Text-labeled Samples: {text_eval_count}")
    print(f"CER (lower is better): {cer_avg:.2f}%")
    print(f"WER (lower is better): {wer_avg:.2f}%")


if __name__ == "__main__":
    main()
