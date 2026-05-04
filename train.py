import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.svm import LinearSVC
from sklearn.calibration import CalibratedClassifierCV
from sklearn.pipeline import Pipeline, FeatureUnion
from sklearn.metrics import classification_report
import pickle

df = pd.read_csv("data.csv")
X = df["문장"]
y = df["카테고리"]

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

# 글자 n-gram + 단어 n-gram 동시에 사용
features = FeatureUnion([
    ("char", TfidfVectorizer(analyzer="char", ngram_range=(2, 4))),
    ("word", TfidfVectorizer(analyzer="word", ngram_range=(1, 2))),
])

# LinearSVC는 짧은 한국어 텍스트 분류에 LogisticRegression보다 강함
# CalibratedClassifierCV로 감싸서 predict_proba 사용 가능하게 함
svc = CalibratedClassifierCV(LinearSVC(C=1.0, max_iter=2000))

model_pipeline = Pipeline([
    ("features", features),
    ("clf", svc),
])

model_pipeline.fit(X_train, y_train)

y_pred = model_pipeline.predict(X_test)

print("=== 학습 완료 ===")
print(classification_report(y_test, y_pred))

print("=== 틀린 문장 목록 ===")
results = pd.DataFrame({"문장": X_test, "실제": y_test, "예측": y_pred})
wrong = results[results["실제"] != results["예측"]].sort_values("실제")
for _, row in wrong.iterrows():
    print(f"[{row['실제']} → {row['예측']}] {row['문장']}")

with open("model.pkl", "wb") as f:
    pickle.dump(model_pipeline, f)

print("\n모델 저장 완료 → model.pkl")
