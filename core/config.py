"""Runtime configuration. Everything env-driven; no game specifics here."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Storage
    database_url: str = "postgresql+psycopg://coachio:coachio@localhost:5432/coachio"
    redis_url: str = "redis://localhost:6379/0"

    # Object store (S3 / MinIO)
    s3_endpoint_url: str = "http://localhost:9000"
    s3_public_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "coachio"
    s3_secret_key: str = "coachio-secret"
    s3_bucket: str = "coachio-frames"
    s3_region: str = "us-east-1"

    # Pipeline / budget
    match_budget_usd: float = 0.25
    enable_stage_2: bool = False
    enable_stage_3: bool = False

    # Auto-clip highlights around important moments (local ffmpeg, $0).
    enable_highlights: bool = True
    highlight_window_s: float = 4.0   # frames within +/- this of the event
    highlight_fps: int = 4            # playback fps of the assembled clip

    # Accounts / usage limits (identity is pluggable - see api/deps.current_user).
    free_match_limit: int = 25        # matches per identity before 402

    # --- session transport (NOT authentication - see core/auth/session.py) ----
    # Signs the session cookie. The default is a development value: anyone with
    # it can mint a session for any account, so production MUST set its own.
    # api/main.py refuses to start with the default when the cookie is marked
    # secure, which is the closest thing to a deploy-time check we have.
    # --- model call limits ----------------------------------------------------
    # Per-attempt socket timeout, and the WALL-CLOCK cap across all retries of one
    # request. The old behaviour was 600s x 5 attempts with no total cap, so a
    # provider outage held a match "processing" for the best part of an hour with
    # the progress bar frozen at 95%. 12 minutes is comfortably longer than a
    # healthy whole-video call and short enough to tell someone what happened.
    gemini_http_timeout_s: float = 420.0
    gemini_http_deadline_s: float = 720.0

    secret_key: str = "dev-only-change-me"
    session_max_age_days: int = 30
    # ".coachfio.com" in production so one cookie covers the hub and every game
    # subdomain. Empty locally: browsers disagree about `Domain=.localhost`, so
    # the cookie stays host-only in dev and the hub keeps its fragment hand-off.
    session_cookie_domain: str = ""
    # HTTPS-only. Off locally, where there is no TLS.
    session_cookie_secure: bool = False

    # OCR: "paddle" | "stub"
    ocr_engine: str = "paddle"

    # Vision (Stages 2 & 3). engine: "anthropic" | "openai" | "ollama" | "stub".
    vision_engine: str = "stub"
    anthropic_api_key: str = ""
    # "openai" engine = ANY OpenAI-compatible provider (Gemini / Qwen-VL / GLM /
    # OpenRouter / local vLLM). Set base_url + key + STAGE*_MODEL to the provider's.
    openai_base_url: str = ""
    openai_api_key: str = ""
    openai_input_usd_per_mtok: float = 0.30   # for cost logging (set to your plan)
    openai_output_usd_per_mtok: float = 1.20
    # Native Gemini WHOLE-VIDEO analysis (SourceType.VIDEO_NATIVE). Uses the same
    # Gemini key as the "openai" engine (openai_api_key). media_res "low" ~ 66
    # video-tokens/sec (cheap); "default" ~ 258/sec (sharper).
    # Map (watch the video, dense observations) = flash; reduce (deep synthesis) =
    # pro. Two passes give the video path the same depth as the frame map-reduce.
    gemini_video_model: str = "gemini-flash-latest"        # watch / observe
    gemini_video_synth_model: str = "gemini-pro-latest"    # deep synthesis
    gemini_video_media_res: str = "medium"                 # low|medium|high|default
    # Pre-compress the video (ffmpeg 720p/15fps, no audio) before uploading to
    # Gemini - a huge upload-speed + cost win with negligible quality loss (Gemini
    # samples ~1fps at medium res anyway). Plus we upload ONCE and read N times.
    gemini_video_compress: bool = True
    # True  = deep mode: watch (dense observations) THEN synthesise a report - best
    #         quality, but several Gemini calls per match.
    # False = SINGLE-CALL mode: one Gemini call over the whole video returns the
    #         coaching report directly. Far fewer requests (no 429 bursts) - use this
    #         when the API is rate-limited or for cheap/fast runs.
    gemini_video_two_pass: bool = True
    # Gate the deterministic scoreboard timeline (many small reads). Off in
    # single-call/low-request setups; the one call reads the score itself instead.
    gemini_score_read: bool = True
    # How many frames the scoreboard timeline samples. Higher = finer goal timing
    # but more requests. ~28 is the "middle mode" sweet spot (accurate score + timed
    # goals for ~28 calls); 60 gives second-accurate timing for the deep mode.
    gemini_score_max_frames: int = 60
    # Scoreboard reads are network-bound, not compute-bound: they are batched and
    # run in parallel. Both are tunable because the ceiling here is the API's rate
    # limit, not our machine - back them off if 429s appear.
    gemini_score_batch: int = 6        # crops per request
    # Sized so the default 60-frame read (62 frames -> 11 requests) still goes out
    # as ONE parallel round; below 11 it becomes two rounds and roughly doubles.
    gemini_score_workers: int = 12     # concurrent requests
    # Self-consistency: watch the match N independent times and keep observations
    # that RECUR across viewings - one-off hallucinations get outvoted. Passes run
    # in PARALLEL, so 2 viewings cost ~one viewing's wall-time (extra $ is bandwidth
    # + a 2nd cheap watch, not latency). 1 = off.
    gemini_video_watch_passes: int = 2
    # Self-learning: each clip flags FC 26 things the model is unsure about; the
    # brain researches a few via Gemini Google-Search grounding and files sourced
    # facts into knowledge/learned.yaml, so it gets smarter over time.
    enable_self_learning: bool = True
    learn_max_gaps_per_run: int = 3                         # cap research calls / clip
    # Deterministic roster reader: OCR the on-ball name badges to build the real
    # squad per side, so the coach can't misattribute an opponent as "your" player.
    # Runs in parallel with the watch (little added wall-time).
    enable_roster_ocr: bool = True
    roster_sample_s: float = 5.0                            # OCR one frame every N seconds
    roster_max_frames: int = 60
    # Deep goal re-read: once the scoreboard timeline pins WHEN each goal happened,
    # re-watch the few seconds around the most important CONCEDED goals with the
    # stronger model for a per-goal breakdown (what broke down / root cause / exact
    # fix). Only a handful of short windows, so it stays cheap + inside the budget.
    gemini_deep_goals: bool = True
    gemini_deep_goals_max: int = 4                          # cap deep reads per match
    gemini_deep_goals_pre_s: float = 6.0                    # seconds of build-up before
    gemini_deep_goals_post_s: float = 2.0                   # seconds after the goal
    gemini_deep_goals_fps: float = 2.0                      # frames/sec in the window
    # Local Ollama server (from a container the host is host.docker.internal).
    ollama_base_url: str = "http://host.docker.internal:11434"
    # -1 = auto (use GPU if available); 0 = force CPU (old GPU driver / CUDA PTX crash).
    ollama_num_gpu: int = -1
    stage2_model: str = "claude-haiku-4-5"   # cheap/fast candidate classifier
    stage3_model: str = "claude-sonnet-5"    # deep tactical read on key moments
    stage2_max_candidates: int = 40          # cap frames sent to the small model
    # Stage 3 = one whole-match coaching report.
    #  - "sample": ONE model call over frames sampled across the match (cheap, coarse).
    #  - "full":   read the WHOLE match in segments (map) then synthesise one report
    #              (reduce). Reads far more frames - use with a cheap/large-context
    #              provider. Every segment call is still budget-checked + charged.
    coaching_mode: str = "full"
    coaching_max_frames: int = 16          # "sample" mode: frames in the single call
    coaching_frame_width: int = 768
    coaching_window_frames: int = 10       # "full" mode: frames per segment (map) call
    coaching_max_windows: int = 24         # "full" mode: cap on segments (bounds spend)

    # API
    api_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- one subdomain per game ---------------------------------------------
    # "fifa.coachfio.com" serves the FC adapter, the bare domain serves the
    # chooser. This is a LOOKUP TABLE, deliberately: the mapping is data, so the
    # core never learns a game id and adding a game stays a config line plus a
    # plugin. Format: "<label>=<game_id>@<edition>", comma separated.
    site_hosts: str = "fifa=ea-fc@26,cs=cs2@2"
    # Labels that mean "no game, show the chooser" rather than a missing site.
    site_root_labels: str = "www,app,coachfio,localhost,127"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]

    @property
    def site_host_map(self) -> dict[str, tuple[str, str]]:
        """label -> (game_id, edition). Malformed entries are skipped rather than
        crashing boot: a typo in an env var should not take the API down."""
        out: dict[str, tuple[str, str]] = {}
        for part in self.site_hosts.split(","):
            part = part.strip()
            if not part or "=" not in part or "@" not in part:
                continue
            label, key = part.split("=", 1)
            gid, _, edition = key.partition("@")
            if label.strip() and gid.strip() and edition.strip():
                out[label.strip().lower()] = (gid.strip(), edition.strip())
        return out

    @property
    def root_label_set(self) -> set[str]:
        return {s.strip().lower() for s in self.site_root_labels.split(",") if s.strip()}


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
