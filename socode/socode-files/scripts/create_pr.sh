#!/usr/bin/env bash
# 브랜치 생성 -> 지정된 파일만 커밋 -> PR 생성
#
# 사용법:
#   bash create_pr.sh <branch_name> <pr_title> <pr_body_file> <file1> [file2 ...]
#
# 전제조건:
#   - git 저장소 내부에서 실행
#   - gh (GitHub CLI) 로그인 완료 상태
#   - GitLab/Bitbucket/사내 Git이면 아래 gh 부분을 해당 CLI로 교체 (예: glab mr create)
#
# 환경변수:
#   BASE_BRANCH  : PR 대상 브랜치 (기본값: 현재 브랜치)
set -euo pipefail

BRANCH_NAME="${1:?사용법: create_pr.sh <branch_name> <pr_title> <pr_body_file> <file1> [file2 ...]}"
PR_TITLE="${2:?PR 제목이 필요합니다}"
PR_BODY_FILE="${3:?PR 본문 파일 경로가 필요합니다}"
shift 3

if [ "$#" -eq 0 ]; then
    echo "[create_pr.sh][ERROR] 커밋할 파일을 최소 1개 지정해야 합니다." >&2
    echo "[create_pr.sh][ERROR] 무관한 변경이 섞이지 않도록 수정한 파일만 명시하세요." >&2
    exit 1
fi

CHANGED_FILES=("$@")

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "[create_pr.sh][ERROR] git 저장소 내부에서 실행해야 합니다." >&2
    exit 1
fi

if [ ! -f "$PR_BODY_FILE" ]; then
    echo "[create_pr.sh][ERROR] PR 본문 파일을 찾을 수 없습니다: $PR_BODY_FILE" >&2
    exit 1
fi

ORIGINAL_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
BASE_BRANCH="${BASE_BRANCH:-$ORIGINAL_BRANCH}"

# 커밋 완료 여부. 커밋 후 실패했다면 브랜치를 절대 삭제하지 않는다(작업 유실 방지).
COMMITTED=0

cleanup_on_error() {
    if [ "$COMMITTED" -eq 1 ]; then
        echo "[create_pr.sh][ERROR] 실패했지만 커밋은 완료되었습니다." >&2
        echo "[create_pr.sh][ERROR] 수정 내용은 브랜치 '$BRANCH_NAME'에 보존되어 있습니다." >&2
        echo "[create_pr.sh][ERROR] push/PR만 수동으로 진행하세요. (git checkout $BRANCH_NAME)" >&2
    else
        echo "[create_pr.sh][ERROR] 실패했습니다. 원래 브랜치($ORIGINAL_BRANCH)로 복귀합니다." >&2
        git checkout "$ORIGINAL_BRANCH" >/dev/null 2>&1 || true
        git branch -D "$BRANCH_NAME" >/dev/null 2>&1 || true
    fi
}
trap cleanup_on_error ERR

# 지정된 파일이 실제로 변경되었는지 확인
if git diff --quiet -- "${CHANGED_FILES[@]}" && \
   git diff --cached --quiet -- "${CHANGED_FILES[@]}"; then
    echo "[create_pr.sh][ERROR] 지정된 파일에 변경사항이 없습니다. PR을 만들지 않습니다." >&2
    trap - ERR
    exit 1
fi

echo "[create_pr.sh] 기준 브랜치: $BASE_BRANCH" >&2
echo "[create_pr.sh] 브랜치 생성: $BRANCH_NAME" >&2
git checkout -b "$BRANCH_NAME"

# 지정된 파일만 스테이징 (git add -A 금지: 무관한 변경 혼입 방지)
git add -- "${CHANGED_FILES[@]}"

echo "[create_pr.sh] 커밋 대상 파일:" >&2
git diff --cached --name-only >&2

git commit -m "$PR_TITLE"
COMMITTED=1

git push -u origin "$BRANCH_NAME"

trap - ERR

if command -v gh >/dev/null 2>&1; then
    gh pr create \
        --base "$BASE_BRANCH" \
        --head "$BRANCH_NAME" \
        --title "$PR_TITLE" \
        --body-file "$PR_BODY_FILE"
else
    echo "[create_pr.sh][WARN] gh CLI가 없습니다. 브랜치는 push되었으니 수동으로 PR을 생성하세요." >&2
    echo "[create_pr.sh][WARN] GitLab이면: glab mr create --source-branch $BRANCH_NAME --target-branch $BASE_BRANCH" >&2
fi

git checkout "$ORIGINAL_BRANCH" >/dev/null 2>&1 || true
echo "[create_pr.sh] 완료. 원래 브랜치($ORIGINAL_BRANCH)로 복귀했습니다." >&2
