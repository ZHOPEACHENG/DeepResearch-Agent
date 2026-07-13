"""Dashboard schemas — stats response for the overview page (T120)."""

from pydantic import BaseModel


class DashboardStatsResponse(BaseModel):
    """Aggregated counts for the dashboard overview cards."""

    totalConversations: int
    activeResearchCount: int
    completedResearchCount: int
    knowledgeDocCount: int

    model_config = {"from_attributes": True}
