from __future__ import annotations

from typing import List, Literal

from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel

from utils.logger import get_logger

logger = get_logger(__name__)

MODEL = "claude-sonnet-4-20250514"

SYSTEM_PROMPT = """You are an expert at generating search queries to find upcoming NYC concert events.
Your task is to produce exactly 40 search queries that will surface the widest possible range of
upcoming concerts in New York City.

Rules:
- Generate EXACTLY 40 queries — no more, no fewer.
- Each query must have a query_type of "broad" or "niche".
- Broad queries (~15): General searches like "upcoming concerts NYC this week",
  "live music NYC tonight", "concerts in New York City this month", "best concerts NYC 2026".
- Niche queries (~25): Venue-specific or artist-specific. Must include dedicated queries
  for each of these venues:
    Madison Square Garden, Barclays Center, Radio City Music Hall, Carnegie Hall,
    Beacon Theatre, Irving Plaza, Webster Hall, Brooklyn Steel, Terminal 5,
    Bowery Ballroom, Mercury Lounge, Music Hall of Williamsburg, SummerStage Central Park,
    Forest Hills Stadium, Jones Beach Amphitheater, Apollo Theater, United Palace.
- Output a JSON array of objects, each with "query" (string) and "query_type" ("broad" or "niche").
"""


class SearchQuery(BaseModel):
    query: str
    query_type: Literal["broad", "niche"]


class SearchPlan(BaseModel):
    queries: List[SearchQuery]


class SearchPlanAgent:
    def __init__(self):
        self._llm = ChatAnthropic(model=MODEL).with_structured_output(SearchPlan)

    def generate(self) -> SearchPlan:
        logger.info("Generating 40-query Search Plan…")
        for attempt in range(2):
            try:
                plan: SearchPlan = self._llm.invoke(
                    [{"role": "user", "content": SYSTEM_PROMPT}]
                )
                if len(plan.queries) != 40:
                    logger.warning(
                        f"Search plan returned {len(plan.queries)} queries (expected 40). "
                        f"Attempt {attempt + 1}/2."
                    )
                    if attempt == 1:
                        raise ValueError(
                            f"LLM produced {len(plan.queries)} queries after 2 attempts; expected 40."
                        )
                    continue
                logger.info(f"Search Plan generated with {len(plan.queries)} queries")
                for i, q in enumerate(plan.queries, 1):
                    logger.debug(f"  [{i:02d}] [{q.query_type}] {q.query}")
                return plan
            except ValueError:
                raise
            except Exception as e:
                logger.error(f"Search Plan LLM call failed on attempt {attempt + 1}: {e}")
                if attempt == 1:
                    raise
        # Should not reach here
        raise RuntimeError("Search Plan generation failed unexpectedly")
