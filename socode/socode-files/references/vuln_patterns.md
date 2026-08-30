# 취약점 유형별 표준 수정 패턴

Claude가 findings.json의 `rule_id`/`cwe`를 보고 참고할 표준 수정 패턴 모음.
사내 CLI의 rule_id 명명 규칙을 알게 되면 아래 표의 매칭 키워드를 조정하세요.

## SQL Injection (CWE-89)
- **매칭 키워드**: SQL_INJECTION, SQLI
- **수정 방향**: 문자열 연결(`+`, format, f-string)로 만든 쿼리를 파라미터 바인딩(PreparedStatement, MyBatis `#{}`, JPA named parameter 등)으로 교체
- **주의**: MyBatis에서 `${}`(치환)를 `#{}`(바인딩)로 바꿀 때, 컬럼/테이블명 동적 지정처럼 바인딩이 불가능한 경우는 화이트리스트 검증으로 대체하고 자동수정 대상에서 제외

## XSS (CWE-79)
- **매칭 키워드**: XSS, CROSS_SITE_SCRIPTING
- **수정 방향**: 사용자 입력을 HTML에 그대로 출력하는 부분에 이스케이핑 적용 (JSP `<c:out>`, Thymeleaf `th:text`, 프론트엔드는 프레임워크의 기본 이스케이핑 활용)
- **주의**: `innerHTML`, `v-html`, `dangerouslySetInnerHTML` 등 명시적 raw HTML 삽입은 정말 필요한 경우인지 확인 후 sanitize 라이브러리 적용

## Path Traversal (CWE-22)
- **매칭 키워드**: PATH_TRAVERSAL, DIRECTORY_TRAVERSAL
- **수정 방향**: 사용자 입력 경로를 `File.getCanonicalPath()` 등으로 정규화 후, 허용된 base 디렉토리 하위인지 검증

## Hardcoded Credentials (CWE-798)
- **매칭 키워드**: HARDCODED_PASSWORD, HARDCODED_SECRET, HARDCODED_CREDENTIALS
- **수정 방향**: 환경변수 또는 사내 secret manager 참조로 교체. **자동수정 시 실제 값은 절대 커밋 로그/PR 본문에 남기지 않는다.**
- **주의**: 이 유형은 credential 순환(rotation)이 필요할 수 있어 medium 이상이어도 사람 확인 권장

## Insecure Deserialization (CWE-502)
- **매칭 키워드**: DESERIALIZATION, UNSAFE_DESERIALIZE
- **수정 방향**: 신뢰할 수 없는 입력의 역직렬화는 허용 클래스 화이트리스트 적용 (Java: `ObjectInputFilter`, Jackson: `PolymorphicTypeValidator`)
- **주의**: 구조 변경이 커서 자동수정보다는 `manual_review_needed` 권장

## Weak Cryptography (CWE-327)
- **매칭 키워드**: WEAK_CRYPTO, WEAK_HASH, MD5, SHA1
- **수정 방향**: MD5/SHA1 등 취약 해시를 SHA-256 이상으로 교체. 비밀번호 저장용이면 반드시 bcrypt/scrypt/Argon2 계열로 교체 (단순 해시 교체 아님)

## Unvalidated Redirect (CWE-601)
- **매칭 키워드**: OPEN_REDIRECT
- **수정 방향**: 리다이렉트 대상 URL을 화이트리스트/상대경로로 제한

## Missing Input Validation (CWE-20)
- **매칭 키워드**: INPUT_VALIDATION, MISSING_VALIDATION
- **수정 방향**: 룰이 포괄적이라 케이스별 판단 필요. 구체적인 하위 취약점(SQLi, XSS 등)으로 다시 분류 가능하면 위 항목 참고, 아니면 자동수정하지 않고 리포트만

---

## 새로운 rule_id를 만났을 때

위 표에 없는 `rule_id`나 `cwe`를 만나면:
1. CWE 번호로 웹 검색해서 표준 대응 패턴 확인
2. 수정 방향이 명확하고 되돌리기 쉬운 변경이면 자동수정 진행
3. 애매하면 `manual_review_needed`로 표시하고 이 파일에 새 항목 추가 제안
