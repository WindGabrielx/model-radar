-- 002_core.sql — dimensions and facts (CLAUDE.md §6.2)

CREATE TABLE IF NOT EXISTS core.dim_vendor (
    vendor_id           text PRIMARY KEY,               -- openai, anthropic, google, deepseek, xai
    name                text NOT NULL,
    openrouter_prefixes text[] NOT NULL,
    color_hex           text NOT NULL,
    hn_keywords         text[] NOT NULL,
    hn_context_terms    text[] NOT NULL DEFAULT '{}'    -- empty = keyword alone is enough
);

CREATE TABLE IF NOT EXISTS core.dim_model (
    model_id         text PRIMARY KEY,                  -- OpenRouter id, e.g. anthropic/claude-opus-5.5
    vendor_id        text NOT NULL REFERENCES core.dim_vendor,
    canonical_slug   text,
    name             text,
    tier             text NOT NULL DEFAULT 'unclassified'
                     CHECK (tier IN ('top','mid','small','unclassified')),
    output_type      text NOT NULL DEFAULT 'text' CHECK (output_type IN ('text','image')),
    created_at_source timestamptz,                      -- OpenRouter "created"
    first_seen_date  date NOT NULL,
    last_seen_date   date NOT NULL,
    is_active        boolean NOT NULL DEFAULT true,
    updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS dim_model_vendor_idx ON core.dim_model (vendor_id);

CREATE TABLE IF NOT EXISTS core.map_model_alias (
    alias        text NOT NULL,
    alias_source text NOT NULL,                         -- canonical_slug, permaslug, api_name_<vendor>
    model_id     text NOT NULL REFERENCES core.dim_model,
    match_method text NOT NULL CHECK (match_method IN ('exact','fuzzy_reviewed','manual')),
    PRIMARY KEY (alias, alias_source)
);

CREATE TABLE IF NOT EXISTS core.fact_model_daily (
    model_id                 text NOT NULL REFERENCES core.dim_model,
    snapshot_date            date NOT NULL,
    context_length           integer,
    max_completion_tokens    integer,
    input_price_per_m        numeric(14,6),
    output_price_per_m       numeric(14,6),
    cache_read_price_per_m   numeric(14,6),
    batch_input_price_per_m  numeric(14,6),
    batch_output_price_per_m numeric(14,6),
    input_modalities         text[],
    supported_parameters     jsonb,
    expiration_date          date,
    loaded_at                timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (model_id, snapshot_date)
);

CREATE TABLE IF NOT EXISTS core.fact_endpoint_daily (
    model_id           text NOT NULL REFERENCES core.dim_model,
    provider_name      text NOT NULL,
    snapshot_date      date NOT NULL,
    input_price_per_m  numeric(14,6),
    output_price_per_m numeric(14,6),
    uptime_last_1d     numeric(6,2),
    latency_p50 numeric, latency_p75 numeric, latency_p90 numeric, latency_p99 numeric,          -- ms
    throughput_p50 numeric, throughput_p75 numeric, throughput_p90 numeric, throughput_p99 numeric, -- tok/s
    loaded_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (model_id, provider_name, snapshot_date)
);

CREATE TABLE IF NOT EXISTS core.fact_usage_daily (
    usage_date      date NOT NULL,
    model_permaslug text NOT NULL,
    model_id        text REFERENCES core.dim_model,
    total_tokens    bigint NOT NULL,
    is_other_row    boolean NOT NULL DEFAULT false,
    loaded_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (usage_date, model_permaslug)
);

CREATE TABLE IF NOT EXISTS core.fact_benchmark_snapshot (
    snapshot_date      date NOT NULL,
    source             text NOT NULL,     -- openrouter_benchmarks | openrouter_models_aa (embedded in /models)
    model_permaslug    text NOT NULL,
    model_id           text REFERENCES core.dim_model,
    intelligence_index numeric,
    coding_index       numeric,
    agentic_index      numeric,
    extra              jsonb,
    citation           text,
    loaded_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (snapshot_date, source, model_permaslug)
);

CREATE TABLE IF NOT EXISTS core.fact_task_share (
    as_of          date NOT NULL,
    task_tag       text NOT NULL,
    name           text,
    macro_category text,
    usage_share    numeric,
    token_share    numeric,
    top_models     jsonb,
    loaded_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (as_of, task_tag)
);

CREATE TABLE IF NOT EXISTS core.fact_lifecycle_event (
    vendor_id      text NOT NULL REFERENCES core.dim_vendor,
    model_api_name text NOT NULL,
    event_type     text NOT NULL CHECK (event_type IN ('released','deprecated','retired','price_change',
                                                       'first_seen_openrouter','removed_from_openrouter')),
    event_date     date NOT NULL,
    model_id       text REFERENCES core.dim_model,
    announced_date date,
    replacement    text,
    detail         text,
    is_tentative   boolean NOT NULL DEFAULT false,   -- "not sooner than" / earliest-possible dates
    source_url     text,
    source_type    text NOT NULL CHECK (source_type IN ('official','derived')),
    loaded_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (vendor_id, model_api_name, event_type, event_date)
);

CREATE TABLE IF NOT EXISTS core.fact_news_item (
    news_id      text PRIMARY KEY,                  -- hn:<objectID>, rss_openai:<sha>, gdelt:<sha>
    source       text NOT NULL CHECK (source IN ('hn','rss_openai','rss_deepmind','gdelt')),
    title        text NOT NULL,
    url          text,
    published_at timestamptz NOT NULL,
    points       integer,
    num_comments integer,
    domain       text,
    loaded_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS news_published_idx ON core.fact_news_item (published_at DESC);

CREATE TABLE IF NOT EXISTS core.bridge_news_vendor (
    news_id    text NOT NULL REFERENCES core.fact_news_item ON DELETE CASCADE,
    vendor_id  text NOT NULL REFERENCES core.dim_vendor,
    match_rule text NOT NULL,
    PRIMARY KEY (news_id, vendor_id)
);

-- aspect = 'overall' holds the comment-level polarity; ABSA aspects per §8.3
CREATE TABLE IF NOT EXISTS core.fact_comment_sentiment (
    comment_id    text NOT NULL,
    vendor_id     text NOT NULL REFERENCES core.dim_vendor,
    aspect        text NOT NULL CHECK (aspect IN ('overall','coding','reasoning','writing','accuracy','speed',
                                                  'pricing','reliability','context_window','user_experience','multimodal')),
    story_id      text,
    published_at  timestamptz,
    polarity      text NOT NULL CHECK (polarity IN ('positive','negative','neutral','mixed')),
    intent        text CHECK (intent IN ('comparison','recommendation','complaint','question','informational')),
    confidence    numeric,
    model_version text NOT NULL,
    loaded_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (comment_id, vendor_id, aspect)
);
