from pydantic import BaseModel, Field


class OrderRequest(BaseModel):
    product_id: int = Field(strict=True, gt=0)
    quantity: int = Field(strict=True, gt=0)
