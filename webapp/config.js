// 공개해도 안전한 값만 넣습니다 (anon 키는 RLS로 읽기 전용이라 노출되어도 무방합니다).
// service_role 키는 절대 이 폴더에 넣지 마세요 - 크롤러(GitHub Secrets)에서만 사용합니다.
window.LIONS_CONFIG = {
  SUPABASE_URL: "https://eakttvuspuoydsuysasa.supabase.co",
  SUPABASE_ANON_KEY:
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVha3R0dnVzcHVveWRzdXlzYXNhIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODU1NDUxMzUsImV4cCI6MjEwMTEyMTEzNX0.fLzW8aNzhexyswHOUoeqeHGSLx0OoCg0e9bDk6Ayyn4",
};
