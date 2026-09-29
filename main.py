import base64
import hashlib
import json
import urllib.request
import re
import os
import smtplib
import threading
import time
from contextlib import contextmanager
from email.message import EmailMessage
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, TIMESTAMP, ForeignKey, Text, Boolean, or_, and_, inspect, text
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, Session
from sqlalchemy.sql import func


DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "Adam01555545813")
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "3306")
DB_NAME = os.environ.get("DB_NAME", "finalproject2")


# ---- Email (SMTP) settings: set these env vars to enable real emails ----
SMTP_HOST = os.environ.get("SMTP_HOST", "")          # e.g. smtp.gmail.com
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")  # for Gmail use an "App Password"
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)

# ---- AI shopping assistant (a real LLM chooses the products) ----
# Option 1 (free, runs on your PC): install Ollama, run `ollama pull gemma3:4b`  -> works with no extra settings
#   (one small model that reads text AND looks at photos, and understands Arabic).
# Option 2: any OpenAI-compatible API (Groq, Gemini, OpenAI...): set AI_BASE_URL, AI_API_KEY, AI_MODEL.
# Option 3: Claude: set ANTHROPIC_API_KEY (and optionally ANTHROPIC_MODEL).
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ASSISTANT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
AI_PROVIDER = os.environ.get("AI_PROVIDER") or ("anthropic" if ANTHROPIC_API_KEY else "openai")
AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
AI_API_KEY = os.environ.get("AI_API_KEY", "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").strip()   # <-- paste your Google AI Studio key here
AI_MODEL = os.environ.get("AI_MODEL", "gemini-3.8-flash")
AI_VISION_MODEL = os.environ.get("AI_VISION_MODEL", "gemini-3.8-flash")  # must be a model that can see images
if AI_PROVIDER != "anthropic" and "11434" not in AI_BASE_URL and (not AI_API_KEY or AI_API_KEY.startswith("PUT_YOUR")):
    print("WARNING: AI_API_KEY is empty - paste your key in main.py (AI_API_KEY line) or set the AI_API_KEY variable.")
AI_TIMEOUT = int(os.environ.get("AI_TIMEOUT", "180"))          # seconds to wait for the model
# Photo captions (lets the assistant "see" listing photos). On a slow PC every photo can take minutes and blocks
# the chat, so it is OFF by default. Turn on with:  set AI_CAPTION=1   (best with a GPU or a cloud API)
AI_CAPTION = os.environ.get("AI_CAPTION", "0") == "1"
MAX_QUESTIONS = 2  # clarifying questions the assistant may ask before it must search

SQLALCHEMY_DATABASE_URL = (
    f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def hash_password(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Customer(Base):
    __tablename__ = "customers"

    customer_id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    email = Column(String(100), unique=True, nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())

    cart_items = relationship("CartItem", back_populates="customer", cascade="all, delete-orphan")
    wishlist_items = relationship("WishlistItem", back_populates="customer", cascade="all, delete-orphan")


class Product(Base):
    __tablename__ = "products"

    product_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    category = Column(String(50), nullable=False, default="Home & kitchen")
    listing_type = Column(String(10), nullable=False, default="sell")
    price_egp = Column(Float, nullable=True)
    price_label = Column(String(50), nullable=False)
    image_url = Column(Text, nullable=True)
    icon = Column(String(10), nullable=True)
    seller_name = Column(String(100), nullable=False, default="You")
    seller_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=True)
    description = Column(Text, nullable=True)
    ai_caption = Column(Text, nullable=True)   # what the AI sees in the photo (used by the shopping assistant)
    created_at = Column(TIMESTAMP, server_default=func.now())


class CartItem(Base):
    __tablename__ = "cart_items"

    cart_item_id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.product_id"), nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    mode = Column(String(10), nullable=False, default="sale")  # 'sale' or 'trade'
    created_at = Column(TIMESTAMP, server_default=func.now())

    customer = relationship("Customer", back_populates="cart_items")
    product = relationship("Product")


class WishlistItem(Base):
    __tablename__ = "wishlist_items"

    wishlist_item_id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.product_id"), nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())

    customer = relationship("Customer", back_populates="wishlist_items")
    product = relationship("Product")


class Order(Base):
    __tablename__ = "orders"

    order_id = Column(Integer, primary_key=True, index=True)
    buyer_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.product_id"), nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    price_label = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    created_at = Column(TIMESTAMP, server_default=func.now())

    product = relationship("Product")


class Message(Base):
    __tablename__ = "messages"

    message_id = Column(Integer, primary_key=True, index=True)
    sender_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False)
    recipient_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.product_id"), nullable=False)
    body = Column(Text, nullable=False)
    image_url = Column(LONGTEXT, nullable=True)   # photo sent with a trade offer (data URL)
    offer_id = Column(Integer, nullable=True)     # set when this message carries a trade offer
    is_read = Column(Boolean, nullable=False, default=False)
    created_at = Column(TIMESTAMP, server_default=func.now())

    sender = relationship("Customer", foreign_keys=[sender_id])
    product = relationship("Product")


class TradeOffer(Base):
    __tablename__ = "trade_offers"

    offer_id = Column(Integer, primary_key=True, index=True)
    buyer_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False)
    seller_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.product_id"), nullable=False)
    image_url = Column(LONGTEXT, nullable=False)
    note = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="pending")  # pending / accepted / rejected
    created_at = Column(TIMESTAMP, server_default=func.now())


class SellerRating(Base):
    """One rating per order, written by the buyer about the seller."""
    __tablename__ = "seller_ratings"

    rating_id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, unique=True, nullable=False)   # no FK: ratings survive if the listing is deleted
    product_id = Column(Integer, nullable=False)
    buyer_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False)
    seller_id = Column(Integer, ForeignKey("customers.customer_id"), nullable=False, index=True)
    stars = Column(Integer, nullable=False)
    comment = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())


Base.metadata.create_all(bind=engine)


def seller_rating_summary(db: Session, seller_id: int) -> dict:
    avg, cnt = db.query(func.avg(SellerRating.stars), func.count(SellerRating.rating_id)).filter(
        SellerRating.seller_id == seller_id
    ).one()
    return {"average": round(float(avg), 1) if cnt else None, "count": int(cnt or 0)}


def seller_reviews(db: Session, seller_id: int, limit: int = 10) -> list:
    rows = (
        db.query(SellerRating, Customer.username, Product.name)
        .join(Customer, Customer.customer_id == SellerRating.buyer_id)
        .outerjoin(Product, Product.product_id == SellerRating.product_id)
        .filter(SellerRating.seller_id == seller_id)
        .order_by(SellerRating.rating_id.desc())
        .limit(limit)
        .all()
    )
    return [{
        "stars": r.stars,
        "comment": r.comment,
        "buyer": username,
        "product_name": pname or "",
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r, username, pname in rows]


def migrate_existing_tables():
    """create_all doesn't add columns to tables that already exist, so add the new ones here."""
    insp = inspect(engine)
    with engine.begin() as conn:
        cart_cols = {c["name"] for c in insp.get_columns("cart_items")}
        if "mode" not in cart_cols:
            conn.execute(text("ALTER TABLE cart_items ADD COLUMN mode VARCHAR(10) NOT NULL DEFAULT 'sale'"))
            conn.execute(text(
                "UPDATE cart_items c JOIN products p ON p.product_id = c.product_id "
                "SET c.mode = 'trade' WHERE p.listing_type = 'trade'"
            ))
        prod_cols = {c["name"] for c in insp.get_columns("products")}
        if "ai_caption" not in prod_cols:
            conn.execute(text("ALTER TABLE products ADD COLUMN ai_caption TEXT NULL"))
        msg_cols = {c["name"] for c in insp.get_columns("messages")}
        if "image_url" not in msg_cols:
            conn.execute(text("ALTER TABLE messages ADD COLUMN image_url LONGTEXT NULL"))
        if "offer_id" not in msg_cols:
            conn.execute(text("ALTER TABLE messages ADD COLUMN offer_id INT NULL"))


migrate_existing_tables()


def remove_seed_products():
    """Remove the built-in sample listings (they have no seller) so the homepage starts empty."""
    db = SessionLocal()
    try:
        seed_ids = [pid for (pid,) in db.query(Product.product_id).filter(Product.seller_id.is_(None)).all()]
        if seed_ids:
            db.query(CartItem).filter(CartItem.product_id.in_(seed_ids)).delete(synchronize_session=False)
            db.query(WishlistItem).filter(WishlistItem.product_id.in_(seed_ids)).delete(synchronize_session=False)
            db.query(Order).filter(Order.product_id.in_(seed_ids)).delete(synchronize_session=False)
            db.query(Message).filter(Message.product_id.in_(seed_ids)).delete(synchronize_session=False)
            db.query(Product).filter(Product.product_id.in_(seed_ids)).delete(synchronize_session=False)
            db.commit()
    finally:
        db.close()


remove_seed_products()


app = FastAPI()

frontend_path = Path(__file__).resolve().parent / "frontend.html"


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class SignupIn(BaseModel):
    username: str
    email: str
    password: str


class LoginIn(BaseModel):
    username: str
    password: str


class ResetPasswordIn(BaseModel):
    username: str
    email: str
    new_password: str


class CartAddIn(BaseModel):
    customer_id: int
    product_id: int
    quantity: int = 1
    mode: str | None = None  # 'sale' or 'trade' (only matters for "both" listings)


class CartModeIn(BaseModel):
    mode: str


class TradeOfferIn(BaseModel):
    customer_id: int
    product_id: int
    image_url: str
    note: str | None = None


class TradeRespondIn(BaseModel):
    customer_id: int
    action: str  # 'accept' or 'reject'


class WishlistToggleIn(BaseModel):
    customer_id: int
    product_id: int


class CheckoutIn(BaseModel):
    customer_id: int


class BuyNowIn(BaseModel):
    customer_id: int
    product_id: int


class MessageIn(BaseModel):
    sender_id: int
    product_id: int
    body: str
    recipient_id: int | None = None  # needed when the seller replies to a buyer


class ProductCreateIn(BaseModel):
    customer_id: int
    name: str
    description: str | None = None
    category: str | None = "Home & kitchen"
    listing_type: str = "sell"
    price_egp: float | None = None
    price_label: str
    image_url: str | None = None


class RatingIn(BaseModel):
    customer_id: int
    order_id: int
    stars: int
    comment: str | None = None


def product_out(p: Product) -> dict:
    return {
        "id": p.product_id,
        "name": p.name,
        "category": p.category,
        "listing_type": p.listing_type,
        "price_label": p.price_label,
        "price_egp": p.price_egp,
        "image_url": p.image_url,
        "icon": p.icon,
        "seller_name": p.seller_name,
        "seller_id": p.seller_id,
        "description": p.description,
    }


@app.get("/")
def root():
    return FileResponse(frontend_path)


@app.get("/api/message")
def message():
    return {"message": "Hello from the FastAPI backend!"}


@app.post("/api/signup")
def signup(body: SignupIn, db: Session = Depends(get_db)):
    if db.query(Customer).filter(Customer.username == body.username).first():
        raise HTTPException(status_code=400, detail="Username already taken.")
    if db.query(Customer).filter(Customer.email == body.email).first():
        raise HTTPException(status_code=400, detail="Email already registered.")
    customer = Customer(
        username=body.username,
        email=body.email,
        password=hash_password(body.password),
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return {"id": customer.customer_id, "username": customer.username, "email": customer.email}


@app.post("/api/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.username == body.username).first()
    if not customer or customer.password != hash_password(body.password):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    return {"id": customer.customer_id, "username": customer.username, "email": customer.email}


@app.post("/api/reset-password")
def reset_password(body: ResetPasswordIn, db: Session = Depends(get_db)):
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")

    customer = (
        db.query(Customer)
        .filter(Customer.username == body.username, Customer.email == body.email)
        .first()
    )
    if not customer:
        raise HTTPException(status_code=400, detail="Username and email do not match.")

    customer.password = hash_password(body.new_password)
    db.commit()
    return {"message": "Password reset successfully."}


@app.get("/customers")
def list_customers(db: Session = Depends(get_db)):
    return db.query(Customer).all()


@app.get("/api/products")
def list_products(db: Session = Depends(get_db)):
    return [product_out(p) for p in db.query(Product).order_by(Product.product_id).all()]


@app.post("/api/products")
def create_product(body: ProductCreateIn, background: BackgroundTasks, db: Session = Depends(get_db)):
    seller = db.query(Customer).filter(Customer.customer_id == body.customer_id).first()
    if not seller:
        raise HTTPException(status_code=404, detail="Customer not found.")
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name is required.")
    if body.listing_type not in ("sell", "trade", "both"):
        raise HTTPException(status_code=400, detail="Listing type must be 'sell', 'trade' or 'both'.")

    product = Product(
        name=body.name.strip(),
        category=(body.category or "Home & kitchen").strip(),
        listing_type=body.listing_type,
        price_egp=body.price_egp,
        price_label=body.price_label,
        image_url=body.image_url,
        icon=None if body.image_url else "📦",
        seller_name=seller.username,
        seller_id=seller.customer_id,
        description=body.description,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    if AI_CAPTION:
        background.add_task(caption_product, product.product_id)   # AI looks at the photo
    return product_out(product)


@app.delete("/api/products/{product_id}")
def delete_product(product_id: int, customer_id: int, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.product_id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    if product.seller_id != customer_id:
        raise HTTPException(status_code=403, detail="You can only delete your own listings.")

    db.query(CartItem).filter(CartItem.product_id == product_id).delete()
    db.query(WishlistItem).filter(WishlistItem.product_id == product_id).delete()
    db.query(Order).filter(Order.product_id == product_id).delete()
    db.query(Message).filter(Message.product_id == product_id).delete()
    db.query(TradeOffer).filter(TradeOffer.product_id == product_id).delete()
    db.delete(product)
    db.commit()
    return {"deleted": True}


@app.get("/api/products/{product_id}")
def get_product(product_id: int, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.product_id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found.")
    related = (
        db.query(Product)
        .filter(Product.category == p.category, Product.product_id != p.product_id)
        .limit(3)
        .all()
    )
    return {
        "product": product_out(p),
        "related": [product_out(r) for r in related],
        "seller_rating": seller_rating_summary(db, p.seller_id) if p.seller_id else None,
    }


def effective_mode(product: Product, mode: str | None) -> str:
    """sell-only items are always 'sale', trade-only items always 'trade', 'both' items follow the choice."""
    if product.listing_type == "trade":
        return "trade"
    if product.listing_type == "sell":
        return "sale"
    return mode if mode in ("sale", "trade") else "sale"


@app.get("/api/cart/{customer_id}")
def get_cart(customer_id: int, db: Session = Depends(get_db)):
    items = db.query(CartItem).filter(CartItem.customer_id == customer_id).order_by(CartItem.cart_item_id).all()
    out = []
    total = 0.0  # only the "for sale" items count towards the checkout total
    for it in items:
        p = it.product
        mode = effective_mode(p, it.mode)
        entry = {
            "cart_item_id": it.cart_item_id,
            "quantity": it.quantity,
            "mode": mode,
            "product": product_out(p),
            "offer": None,
        }
        if mode == "sale":
            total += (p.price_egp or 0) * it.quantity
        else:
            offer = (
                db.query(TradeOffer)
                .filter(TradeOffer.buyer_id == customer_id, TradeOffer.product_id == p.product_id)
                .order_by(TradeOffer.offer_id.desc())
                .first()
            )
            if offer:
                entry["offer"] = {"id": offer.offer_id, "status": offer.status}
        out.append(entry)
    return {"items": out, "total_egp": total}


@app.post("/api/cart")
def add_to_cart(body: CartAddIn, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.product_id == body.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    mode = effective_mode(product, body.mode)
    existing = (
        db.query(CartItem)
        .filter(CartItem.customer_id == body.customer_id, CartItem.product_id == body.product_id)
        .first()
    )
    if not existing:
        db.add(CartItem(customer_id=body.customer_id, product_id=body.product_id, quantity=1, mode=mode))
    db.commit()
    return get_cart(body.customer_id, db)


@app.patch("/api/cart/{cart_item_id}/mode")
def set_cart_mode(cart_item_id: int, body: CartModeIn, db: Session = Depends(get_db)):
    item = db.query(CartItem).filter(CartItem.cart_item_id == cart_item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Cart item not found.")
    if body.mode not in ("sale", "trade"):
        raise HTTPException(status_code=400, detail="Mode must be 'sale' or 'trade'.")
    if item.product.listing_type != "both":
        raise HTTPException(status_code=400, detail="This listing only supports one option.")
    item.mode = body.mode
    db.commit()
    return get_cart(item.customer_id, db)


@app.delete("/api/cart/{cart_item_id}")
def remove_from_cart(cart_item_id: int, db: Session = Depends(get_db)):
    item = db.query(CartItem).filter(CartItem.cart_item_id == cart_item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Cart item not found.")
    customer_id = item.customer_id
    db.delete(item)
    db.commit()
    return get_cart(customer_id, db)


@app.post("/api/cart/checkout")
def checkout(body: CheckoutIn, db: Session = Depends(get_db)):
    items = db.query(CartItem).filter(CartItem.customer_id == body.customer_id).all()
    # trade items are NOT checked out - they are settled through trade offers in the chat
    sale_items = [it for it in items if effective_mode(it.product, it.mode) == "sale"]
    if not sale_items:
        raise HTTPException(status_code=400, detail="Cart is empty.")
    for it in sale_items:
        db.add(Order(
            buyer_id=body.customer_id,
            product_id=it.product_id,
            quantity=it.quantity,
            price_label=it.product.price_label,
            status="pending",
        ))
        db.delete(it)
    db.commit()
    return get_bought(body.customer_id, db)


@app.get("/api/wishlist/{customer_id}")
def get_wishlist(customer_id: int, db: Session = Depends(get_db)):
    items = db.query(WishlistItem).filter(WishlistItem.customer_id == customer_id).all()
    return [product_out(it.product) for it in items]


@app.post("/api/wishlist/toggle")
def toggle_wishlist(body: WishlistToggleIn, db: Session = Depends(get_db)):
    existing = (
        db.query(WishlistItem)
        .filter(WishlistItem.customer_id == body.customer_id, WishlistItem.product_id == body.product_id)
        .first()
    )
    if existing:
        db.delete(existing)
        db.commit()
        return {"active": False}
    db.add(WishlistItem(customer_id=body.customer_id, product_id=body.product_id))
    db.commit()
    return {"active": True}


@app.get("/api/orders/bought/{customer_id}")
def get_bought(customer_id: int, db: Session = Depends(get_db)):
    orders = db.query(Order).filter(Order.buyer_id == customer_id).order_by(Order.order_id.desc()).all()
    rated = {}
    if orders:
        rated = {
            r.order_id: r
            for r in db.query(SellerRating).filter(SellerRating.order_id.in_([o.order_id for o in orders])).all()
        }
    out = []
    for o in orders:
        r = rated.get(o.order_id)
        out.append({
            "order_id": o.order_id,
            "status": o.status,
            "quantity": o.quantity,
            "price_label": o.price_label,
            "product": product_out(o.product),
            "rating": {"stars": r.stars, "comment": r.comment} if r else None,
        })
    return out


@app.get("/api/orders/sold/{customer_id}")
def get_sold(customer_id: int, db: Session = Depends(get_db)):
    orders = (
        db.query(Order)
        .join(Product, Order.product_id == Product.product_id)
        .filter(Product.seller_id == customer_id)
        .order_by(Order.order_id.desc())
        .all()
    )
    return [{
        "order_id": o.order_id,
        "status": o.status,
        "quantity": o.quantity,
        "price_label": o.price_label,
        "product": product_out(o.product),
    } for o in orders]


@app.get("/api/profile-stats/{customer_id}")
def profile_stats(customer_id: int, db: Session = Depends(get_db)):
    listings = db.query(Product).filter(Product.seller_id == customer_id).count()
    sold = (
        db.query(Order)
        .join(Product, Order.product_id == Product.product_id)
        .filter(Product.seller_id == customer_id)
        .count()
    )
    return {
        "listings": listings,
        "sold": sold,
        "rating": seller_rating_summary(db, customer_id),   # average is None when nobody rated yet
        "reviews": seller_reviews(db, customer_id),
    }


# ------------------------------------------------------------------
# Email helper
# ------------------------------------------------------------------
def send_email(to_addr: str, subject: str, body: str, reply_to: str | None = None):
    """Send a plain-text email. Silently skipped if SMTP isn't configured."""
    if not (SMTP_HOST and SMTP_FROM and to_addr and "@" in to_addr):
        return
    try:
        msg = EmailMessage()
        msg["From"] = SMTP_FROM
        msg["To"] = to_addr
        msg["Subject"] = subject
        if reply_to and "@" in reply_to:
            msg["Reply-To"] = reply_to
        msg.set_content(body)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
    except Exception as e:  # never break the API because of email problems
        print("Email send failed:", e)


def create_message(db: Session, background: BackgroundTasks, sender: Customer,
                   recipient: Customer, product: Product, text: str, subject_prefix: str,
                   image_url: str | None = None, offer_id: int | None = None):
    """Store a chat message and also email it to the address the recipient registered with."""
    db.add(Message(
        sender_id=sender.customer_id,
        recipient_id=recipient.customer_id,
        product_id=product.product_id,
        body=text,
        image_url=image_url,
        offer_id=offer_id,
    ))
    db.commit()
    background.add_task(
        send_email,
        recipient.email,
        f"{subject_prefix}: {product.name}",
        f"Hi {recipient.username},\n\n{sender.username} sent you a message about \"{product.name}\":\n\n"
        f"{text}\n\nOpen SOUQ > Chat to reply, or reply to this email (their email: {sender.email}).\n\n- SOUQ",
        sender.email,
    )


# ------------------------------------------------------------------
# Buy now
# ------------------------------------------------------------------
@app.post("/api/buy-now")
def buy_now(body: BuyNowIn, background: BackgroundTasks, db: Session = Depends(get_db)):
    buyer = db.query(Customer).filter(Customer.customer_id == body.customer_id).first()
    if not buyer:
        raise HTTPException(status_code=404, detail="Customer not found.")
    product = db.query(Product).filter(Product.product_id == body.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    if product.seller_id == buyer.customer_id:
        raise HTTPException(status_code=400, detail="You can't buy your own listing.")
    if product.listing_type == "trade":
        raise HTTPException(status_code=400, detail="This item is swap only. Send a trade offer instead.")

    # no "already sold" check: the same item can be bought as many times as you like
    db.add(Order(
        buyer_id=buyer.customer_id,
        product_id=product.product_id,
        quantity=1,
        price_label=product.price_label,
        status="pending",
    ))
    # remove it from the buyer's cart if it was there
    db.query(CartItem).filter(
        CartItem.customer_id == buyer.customer_id, CartItem.product_id == product.product_id
    ).delete()
    db.commit()

    seller = db.query(Customer).filter(Customer.customer_id == product.seller_id).first()
    if seller:
        create_message(db, background, buyer, seller, product,
                       "I just bought this item. Let's arrange the details!", "New order")
    return get_bought(buyer.customer_id, db)


# ------------------------------------------------------------------
# Trade offers: buyer uploads a photo of the item they want to swap,
# it lands in the seller's chat, and the seller accepts or rejects it.
# ------------------------------------------------------------------
IMAGE_DATA_URL = re.compile(r"^data:image/(jpeg|jpg|png|webp|gif);base64,", re.I)


@app.post("/api/trade-offers")
def create_trade_offer(body: TradeOfferIn, background: BackgroundTasks, db: Session = Depends(get_db)):
    buyer = db.query(Customer).filter(Customer.customer_id == body.customer_id).first()
    if not buyer:
        raise HTTPException(status_code=404, detail="Customer not found.")
    product = db.query(Product).filter(Product.product_id == body.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    if product.seller_id is None:
        raise HTTPException(status_code=400, detail="This listing has no seller.")
    if product.seller_id == buyer.customer_id:
        raise HTTPException(status_code=400, detail="You can't trade with your own listing.")
    if product.listing_type not in ("trade", "both"):
        raise HTTPException(status_code=400, detail="This item is not open for trade.")
    if not IMAGE_DATA_URL.match(body.image_url or ""):
        raise HTTPException(status_code=400, detail="Please upload a photo of your item.")
    if len(body.image_url) > 8_000_000:
        raise HTTPException(status_code=400, detail="The photo is too large.")
    if db.query(TradeOffer).filter(
        TradeOffer.buyer_id == buyer.customer_id,
        TradeOffer.product_id == product.product_id,
        TradeOffer.status == "pending",
    ).first():
        raise HTTPException(status_code=400, detail="You already have a pending offer for this item.")

    seller = db.query(Customer).filter(Customer.customer_id == product.seller_id).first()
    if not seller:
        raise HTTPException(status_code=404, detail="Seller not found.")

    note = (body.note or "").strip()[:300]
    offer = TradeOffer(
        buyer_id=buyer.customer_id,
        seller_id=seller.customer_id,
        product_id=product.product_id,
        image_url=body.image_url,
        note=note or None,
        status="pending",
    )
    db.add(offer)

    # make sure the item sits in the buyer's "trade" list
    cart_item = db.query(CartItem).filter(
        CartItem.customer_id == buyer.customer_id, CartItem.product_id == product.product_id
    ).first()
    if cart_item:
        cart_item.mode = "trade"
    else:
        db.add(CartItem(customer_id=buyer.customer_id, product_id=product.product_id, quantity=1, mode="trade"))
    db.flush()

    text_body = f"🔄 Trade offer: I'd like to swap my item for \"{product.name}\"."
    if note:
        text_body += f"\n{note}"
    create_message(db, background, buyer, seller, product, text_body, "New trade offer",
                   image_url=body.image_url, offer_id=offer.offer_id)
    return get_cart(buyer.customer_id, db)


@app.post("/api/trade-offers/{offer_id}/respond")
def respond_trade_offer(offer_id: int, body: TradeRespondIn, background: BackgroundTasks,
                        db: Session = Depends(get_db)):
    offer = db.query(TradeOffer).filter(TradeOffer.offer_id == offer_id).first()
    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found.")
    if offer.seller_id != body.customer_id:
        raise HTTPException(status_code=403, detail="Only the seller can answer this offer.")
    if offer.status != "pending":
        raise HTTPException(status_code=400, detail="This offer was already answered.")
    if body.action not in ("accept", "reject"):
        raise HTTPException(status_code=400, detail="Action must be 'accept' or 'reject'.")

    product = db.query(Product).filter(Product.product_id == offer.product_id).first()
    seller = db.query(Customer).filter(Customer.customer_id == offer.seller_id).first()
    buyer = db.query(Customer).filter(Customer.customer_id == offer.buyer_id).first()
    if not (product and seller and buyer):
        raise HTTPException(status_code=404, detail="Offer not found.")

    if body.action == "accept":
        offer.status = "accepted"
        db.add(Order(
            buyer_id=buyer.customer_id,
            product_id=product.product_id,
            quantity=1,
            price_label="Trade",
            status="pending",
        ))
        db.query(CartItem).filter(
            CartItem.customer_id == buyer.customer_id, CartItem.product_id == product.product_id
        ).delete()
        # the item can be swapped more than once, so other pending offers stay open
        db.commit()
        create_message(db, background, seller, buyer, product,
                       "✅ I accepted your trade offer. Let's arrange the swap!", "Trade offer accepted")
    else:
        offer.status = "rejected"
        db.commit()
        create_message(db, background, seller, buyer, product,
                       "❌ Sorry, I rejected your trade offer.", "Trade offer rejected")

    return {"status": offer.status}


@app.get("/api/messages/{message_id}/image")
def message_image(message_id: int, db: Session = Depends(get_db)):
    m = db.query(Message).filter(Message.message_id == message_id).first()
    if not m or not m.image_url:
        raise HTTPException(status_code=404, detail="Image not found.")
    head, _, data = m.image_url.partition(",")
    media_type = head[5:].split(";")[0] or "image/jpeg"
    if not media_type.startswith("image/"):
        raise HTTPException(status_code=404, detail="Image not found.")
    try:
        content = base64.b64decode(data)
    except Exception:
        raise HTTPException(status_code=404, detail="Image not found.")
    return Response(content=content, media_type=media_type,
                    headers={"Cache-Control": "private, max-age=86400"})


# ------------------------------------------------------------------
# Contact seller + notifications
# ------------------------------------------------------------------
@app.post("/api/messages")
def send_message(body: MessageIn, background: BackgroundTasks, db: Session = Depends(get_db)):
    text = body.body.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message can't be empty.")
    if len(text) > 1000:
        raise HTTPException(status_code=400, detail="Message is too long (max 1000 characters).")
    sender = db.query(Customer).filter(Customer.customer_id == body.sender_id).first()
    if not sender:
        raise HTTPException(status_code=404, detail="Customer not found.")
    product = db.query(Product).filter(Product.product_id == body.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    if product.seller_id is None:
        raise HTTPException(status_code=400, detail="This listing has no seller.")

    if sender.customer_id == product.seller_id:
        # the seller is replying to a buyer who already wrote to them
        if not body.recipient_id:
            raise HTTPException(status_code=400, detail="Choose who to reply to.")
        recipient = db.query(Customer).filter(Customer.customer_id == body.recipient_id).first()
        if not recipient or recipient.customer_id == sender.customer_id:
            raise HTTPException(status_code=404, detail="Customer not found.")
        had_conversation = db.query(Message).filter(
            Message.product_id == product.product_id,
            Message.sender_id == recipient.customer_id,
            Message.recipient_id == sender.customer_id,
        ).first()
        if not had_conversation:
            raise HTTPException(status_code=403, detail="No conversation with this user.")
    else:
        recipient = db.query(Customer).filter(Customer.customer_id == product.seller_id).first()
        if not recipient:
            raise HTTPException(status_code=404, detail="Seller not found.")

    create_message(db, background, sender, recipient, product, text, "New message")
    return {"sent": True}


@app.get("/api/notifications/{customer_id}")
def get_notifications(customer_id: int, db: Session = Depends(get_db)):
    rows = (
        db.query(Message)
        .filter(Message.recipient_id == customer_id)
        .order_by(Message.message_id.desc())
        .limit(50)
        .all()
    )
    unread = db.query(Message).filter(
        Message.recipient_id == customer_id, Message.is_read.is_(False)
    ).count()
    return {
        "unread": unread,
        "items": [{
            "id": m.message_id,
            "sender_username": m.sender.username if m.sender else "Unknown",
            "product_id": m.product_id,
            "product_name": m.product.name if m.product else "",
            "body": m.body,
            "is_read": bool(m.is_read),
            "created_at": m.created_at.isoformat() if m.created_at else None,
        } for m in rows],
    }


@app.post("/api/notifications/{customer_id}/read")
def mark_notifications_read(customer_id: int, db: Session = Depends(get_db)):
    db.query(Message).filter(
        Message.recipient_id == customer_id, Message.is_read.is_(False)
    ).update({"is_read": True}, synchronize_session=False)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------
# Chat: conversation list, thread, unread counter
# A conversation = (me, other user, product)
# ------------------------------------------------------------------
@app.get("/api/chats/{customer_id}/unread")
def chats_unread(customer_id: int, db: Session = Depends(get_db)):
    n = db.query(Message).filter(
        Message.recipient_id == customer_id, Message.is_read.is_(False)
    ).count()
    return {"unread": n}


@app.get("/api/chats/{customer_id}/thread")
def chat_thread(customer_id: int, other_id: int, product_id: int, db: Session = Depends(get_db)):
    pair = or_(
        and_(Message.sender_id == customer_id, Message.recipient_id == other_id),
        and_(Message.sender_id == other_id, Message.recipient_id == customer_id),
    )
    msgs = (
        db.query(Message)
        .filter(Message.product_id == product_id, pair)
        .order_by(Message.message_id.asc())
        .all()
    )
    out = []
    for m in msgs:
        offer_info = None
        if m.offer_id:
            offer = db.query(TradeOffer).filter(TradeOffer.offer_id == m.offer_id).first()
            if offer:
                offer_info = {
                    "id": offer.offer_id,
                    "status": offer.status,
                    "can_respond": offer.seller_id == customer_id and offer.status == "pending",
                }
        out.append({
            "id": m.message_id,
            "mine": m.sender_id == customer_id,
            "body": m.body,
            "has_image": bool(m.image_url),
            "offer": offer_info,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        })
    db.query(Message).filter(
        Message.product_id == product_id,
        Message.sender_id == other_id,
        Message.recipient_id == customer_id,
        Message.is_read.is_(False),
    ).update({"is_read": True}, synchronize_session=False)
    db.commit()
    return out


@app.get("/api/chats/{customer_id}")
def list_chats(customer_id: int, db: Session = Depends(get_db)):
    msgs = (
        db.query(Message)
        .filter(or_(Message.sender_id == customer_id, Message.recipient_id == customer_id))
        .order_by(Message.message_id.desc())
        .all()
    )
    convs: dict = {}
    names: dict = {}
    for m in msgs:
        other_id = m.recipient_id if m.sender_id == customer_id else m.sender_id
        key = (other_id, m.product_id)
        c = convs.get(key)
        if c is None:
            if other_id not in names:
                other = db.query(Customer).filter(Customer.customer_id == other_id).first()
                names[other_id] = other.username if other else "Unknown"
            c = {
                "other_id": other_id,
                "other_username": names[other_id],
                "product_id": m.product_id,
                "product_name": m.product.name if m.product else "",
                "last_body": m.body,
                "last_time": m.created_at.isoformat() if m.created_at else None,
                "unread": 0,
            }
            convs[key] = c  # newest message comes first, so dict order = newest conversation first
        if m.recipient_id == customer_id and not m.is_read:
            c["unread"] += 1
    return list(convs.values())


# ------------------------------------------------------------------
# Seller ratings (buyer rates the seller after buying)
# ------------------------------------------------------------------
@app.post("/api/ratings")
def rate_seller(body: RatingIn, db: Session = Depends(get_db)):
    if not 1 <= body.stars <= 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5 stars.")
    order = db.query(Order).filter(Order.order_id == body.order_id).first()
    if not order or order.buyer_id != body.customer_id:
        raise HTTPException(status_code=403, detail="You can only rate your own orders.")
    product = order.product
    if not product or product.seller_id is None:
        raise HTTPException(status_code=400, detail="This listing has no seller to rate.")
    if product.seller_id == body.customer_id:
        raise HTTPException(status_code=400, detail="You can't rate yourself.")
    if db.query(SellerRating).filter(SellerRating.order_id == order.order_id).first():
        raise HTTPException(status_code=400, detail="You already rated this order.")

    comment = (body.comment or "").strip()[:500]
    db.add(SellerRating(
        order_id=order.order_id,
        product_id=product.product_id,
        buyer_id=body.customer_id,
        seller_id=product.seller_id,
        stars=body.stars,
        comment=comment or None,
    ))
    db.commit()
    return {
        "rating": {"stars": body.stars, "comment": comment or None},
        "seller": seller_rating_summary(db, product.seller_id),
    }


# ------------------------------------------------------------------
# AI shopping assistant
# "phone for studying, around 8000"  ->  asks what is missing  ->  searches the products table
# ------------------------------------------------------------------
class ChatTurn(BaseModel):
    role: str
    content: str


class AssistantIn(BaseModel):
    messages: list[ChatTurn]
    asked: int = 0                  # clarifying questions already asked in this search
    customer_id: int | None = None  # so the assistant never suggests your own listings


ASSISTANT_SYSTEM = """You are the shopping assistant of SOUQ, a marketplace where people in Egypt sell and swap items (prices are in EGP).
You are given the CATALOG of items currently listed. Reply with ONE JSON object only (no markdown, no extra text):
{"action":"ask"|"results","reply":"...","options":["..."],"product_ids":[1,2],"max_price":null}
Rules:
- Write "reply" in the user's language and dialect (Egyptian Arabic if they write Egyptian Arabic). Short, friendly, natural.
- Understand what the person really wants (product type, budget, use, brand, condition).
- Each CATALOG line ends with "photo: ..." = what the picture of the item REALLY shows. Use it together with the name and description. If the name and the photo disagree, or the photo shows a different kind of item than requested, do NOT return it. Only return an item when BOTH its text and its photo fit the request. In "reply", talk only about items that are in "product_ids".
- If an important detail is missing, use action "ask": ask ONE short question and give 2-4 short tap-able answers in "options". Never ask about something already answered. You may ask at most __MAX_Q__ questions in total; if questions_asked >= __MAX_Q__, or you already know the product type plus a budget or a use, use action "results".
- For "results": put in "product_ids" ONLY ids from the CATALOG that truly are the kind of product requested, best fit first, at most 6. NEVER add unrelated items to fill the list (a smartwatch request must not return a plant). If nothing matches, return an empty list and say honestly that there is nothing like that right now (you may suggest a close alternative in words). Respect the budget: "max_price" is a plain number (convert 8000, ٨٠٠٠, 8k, "٨ آلاف" to 8000) or null.
- In "reply" for results, add one short sentence on why the top pick fits. Do not invent specs that are not in the catalog.
- If the user is not looking for something to buy, use action "ask" and politely ask what they need."""


def is_arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", text or ""))


def http_json(url: str, payload: dict, headers: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=AI_TIMEOUT) as resp:  # local models can be slow on the first call
            return json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            body = ""
        raise RuntimeError(f"HTTP {e.code} from {url} -> {body}") from None


def call_llm(system: str, messages: list) -> dict:
    if AI_PROVIDER == "anthropic":
        data = http_json(
            "https://api.anthropic.com/v1/messages",
            {"model": ASSISTANT_MODEL, "max_tokens": 700, "system": system, "messages": messages},
            {"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
        )
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    else:  # any OpenAI-compatible server: Ollama, Groq, Gemini, OpenAI...
        data = http_json(
            AI_BASE_URL.rstrip("/") + "/chat/completions",
            {"model": AI_MODEL, "temperature": 0.2, "max_tokens": 600, "response_format": {"type": "json_object"},
             "messages": [{"role": "system", "content": system}] + messages},
            {"Authorization": "Bearer " + AI_API_KEY} if AI_API_KEY else {},
        )
        text = data["choices"][0]["message"]["content"]
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("model did not return JSON: " + text[:120])
    return json.loads(match.group(0))


OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "60m")


def warm_up_model():
    """Load the local Ollama model into memory now (and keep it there) so the first user request isn't slow."""
    if AI_PROVIDER == "anthropic" or "11434" not in AI_BASE_URL:
        return
    root = AI_BASE_URL.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    for model in {AI_MODEL, AI_VISION_MODEL}:
        try:
            http_json(root + "/api/generate", {"model": model, "prompt": "", "keep_alive": OLLAMA_KEEP_ALIVE}, {})
            print("Warmed up model:", model)
        except Exception as e:
            print("Model warm-up skipped:", e)


ai_lock = threading.Lock()
ai_requests_active = 0   # user requests waiting on the AI right now


@contextmanager
def ai_busy():
    """Mark that a real user is waiting, so background photo captioning steps aside."""
    global ai_requests_active
    with ai_lock:
        ai_requests_active += 1
    try:
        yield
    finally:
        with ai_lock:
            ai_requests_active -= 1


def background_ai_setup():
    warm_up_model()
    if AI_CAPTION:
        caption_missing_products()


@app.on_event("startup")
def start_warm_up():
    threading.Thread(target=background_ai_setup, daemon=True).start()


def to_number(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


@app.post("/api/assistant")
def shopping_assistant(body: AssistantIn, db: Session = Depends(get_db)):
    msgs = [
        {"role": m.role, "content": m.content.strip()[:1000]}
        for m in body.messages[-12:]
        if m.role in ("user", "assistant") and m.content.strip()
    ]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if not msgs or msgs[-1]["role"] != "user":
        raise HTTPException(status_code=400, detail="Write what you're looking for.")
    asked = max(0, min(body.asked, 10))
    ar = is_arabic(msgs[-1]["content"])

    # the model sees the real catalog and picks from it
    q = db.query(Product).filter(Product.seller_id.isnot(None))
    if body.customer_id:
        q = q.filter(Product.seller_id != body.customer_id)
    products = q.order_by(Product.product_id.desc()).limit(60).all()
    by_id = {p.product_id: p for p in products}
    catalog = "\n".join(
        f"{p.product_id} | {p.name[:50]} | {int(p.price_egp) if p.price_egp else 'swap only'} | "
        f"{p.listing_type} | {p.category} | {(p.description or '')[:40].replace(chr(10), ' ')} | "
        f"photo: {(p.ai_caption or ('not analyzed yet' if p.image_url else 'no photo'))[:100]}"
        for p in products
    ) or "(no items listed yet)"
    system = (
        ASSISTANT_SYSTEM.replace("__MAX_Q__", str(MAX_QUESTIONS))
        + f"\nquestions_asked = {asked}\n"
        + ("You MUST NOT ask any more questions now: answer with action \"results\".\n" if asked >= MAX_QUESTIONS else "")
        + "\nCATALOG (id | name | price EGP | listing type | category | description | photo):\n" + catalog
    )

    try:
        with ai_busy():
            plan = call_llm(system, msgs)
    except Exception as e:
        print("Assistant LLM failed:", e)
        why = f" [{type(e).__name__}: {str(e)[:400]}]"   # debug info: delete this line when everything works
        raise HTTPException(status_code=503, detail=(
            ("المساعد الذكي مش متوصل دلوقتي. شغّل Ollama أو حط مفتاح API وجرّب تاني." if ar
             else "The AI model isn't reachable right now. Start Ollama or set an API key, then try again.") + why))

    if plan.get("action") == "ask" and asked < MAX_QUESTIONS:
        options = [str(o).strip()[:40] for o in (plan.get("options") or []) if str(o).strip()][:4]
        return {"type": "ask", "reply": str(plan.get("reply") or "")[:500], "options": options, "products": []}

    mx = to_number(plan.get("max_price"))
    picked = []
    for pid in plan.get("product_ids") or []:
        try:
            p = by_id.get(int(pid))
        except (TypeError, ValueError):
            continue
        if not p or p in picked:
            continue
        if mx and p.price_egp is not None and p.price_egp > mx:
            continue
        picked.append(p)
    picked = picked[:6]

    reply = str(plan.get("reply") or "").strip()[:500]
    if not reply:
        reply = (("دي أنسب حاجات لقيتها ليك 👇" if picked else "مفيش حاجة مطابقة دلوقتي.") if ar
                 else ("Here's what I found for you 👇" if picked else "Nothing matches right now."))
    return {"type": "results", "reply": reply, "options": [], "products": [product_out(p) for p in picked]}


# ------------------------------------------------------------------
# "Generate" buttons in the sell form: the AI looks at the photo and writes the name / description
# ------------------------------------------------------------------
class DescribeIn(BaseModel):
    customer_id: int
    image_url: str
    what: str = "both"        # 'name' | 'description' | 'both'
    lang: str = "en"          # 'ar' or 'en' (the language the site is shown in)
    name: str | None = None   # name the seller already typed, keeps the description consistent


DESCRIBE_SYSTEM = """You help sellers write listings for SOUQ, a marketplace in Egypt. Look at the photo of the item and reply with ONE JSON object only (no markdown): {"name":"...","description":"..."}
- name: short and specific, 2-6 words: what the item is (add colour, material or brand only if clearly visible). No emojis, no quotes, no price.
- description: 1-3 short sentences that help a buyer: what it is, colour, material, visible condition. Describe ONLY what you can actually see. Never invent a brand, model, size, specs, warranty or price. If the seller already gave a name, stay consistent with it.
- If the photo does not show an item that could be sold, set both fields to "".
- Write both fields in __LANG__."""


def call_vision(system: str, text: str, data_url: str) -> dict:
    head, _, b64 = data_url.partition(",")
    media = (head[5:].split(";")[0] or "image/jpeg").replace("image/jpg", "image/jpeg")
    if AI_PROVIDER == "anthropic":
        data = http_json(
            "https://api.anthropic.com/v1/messages",
            {"model": ASSISTANT_MODEL, "max_tokens": 400, "system": system, "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}},
                    {"type": "text", "text": text},
                ],
            }]},
            {"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
        )
        out = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    else:  # OpenAI-compatible (Ollama, Groq, Gemini, OpenAI...)
        data = http_json(
            AI_BASE_URL.rstrip("/") + "/chat/completions",
            {"model": AI_VISION_MODEL, "temperature": 0.3, "max_tokens": 250, "response_format": {"type": "json_object"},
             "messages": [
                 {"role": "system", "content": system},
                 {"role": "user", "content": [
                     {"type": "text", "text": text},
                     {"type": "image_url", "image_url": {"url": data_url}},
                 ]},
             ]},
            {"Authorization": "Bearer " + AI_API_KEY} if AI_API_KEY else {},
        )
        out = data["choices"][0]["message"]["content"]
    match = re.search(r"\{.*\}", out, re.S)
    if not match:
        raise ValueError("model did not return JSON: " + out[:120])
    return json.loads(match.group(0))


CAPTION_SYSTEM = """You look at the photo of an item listed on a marketplace. Reply with ONE JSON object only (no markdown): {"caption":"..."}
- caption: in English, 6-15 words, saying exactly what the item in the photo is (type, colour, material; brand only if clearly visible). Describe ONLY what you can actually see.
- If the photo does not show a sellable item, use "no clear product"."""


def caption_product(product_id: int):
    """Ask the vision model what a listing's photo shows and save it, so the shopping assistant can 'see' the item."""
    db = SessionLocal()
    try:
        p = db.query(Product).filter(Product.product_id == product_id).first()
        if not p or not p.image_url or not IMAGE_DATA_URL.match(p.image_url):
            return
        result = call_vision(CAPTION_SYSTEM, "What is in this photo?", p.image_url)
        caption = str(result.get("caption") or "").strip()[:120]
        if caption:
            p.ai_caption = caption
            db.commit()
            print(f"Captioned product {product_id}: {caption}")
    except Exception as e:
        print(f"Captioning product {product_id} failed:", e)
    finally:
        db.close()


def caption_missing_products():
    db = SessionLocal()
    try:
        ids = [pid for (pid,) in db.query(Product.product_id).filter(
            Product.image_url.isnot(None), Product.ai_caption.is_(None)).all()]
    finally:
        db.close()
    for pid in ids:
        while ai_requests_active:      # a user is chatting: let them go first
            time.sleep(2)
        caption_product(pid)


@app.post("/api/describe-image")
def describe_image(body: DescribeIn, db: Session = Depends(get_db)):
    ar = body.lang == "ar"
    if not db.query(Customer).filter(Customer.customer_id == body.customer_id).first():
        raise HTTPException(status_code=404, detail="Customer not found.")
    if not IMAGE_DATA_URL.match(body.image_url or ""):
        raise HTTPException(status_code=400, detail="Please add a photo first.")
    if len(body.image_url) > 8_000_000:
        raise HTTPException(status_code=400, detail="The photo is too large.")
    if body.what not in ("name", "description", "both"):
        raise HTTPException(status_code=400, detail="Invalid request.")

    system = DESCRIBE_SYSTEM.replace("__LANG__", "simple, natural Egyptian Arabic" if ar else "English")
    hint = (body.name or "").strip()[:150] if body.what == "description" else ""
    text = "Write the listing for this item." + (f' The seller named it: "{hint}".' if hint else "")

    try:
        with ai_busy():
            result = call_vision(system, text, body.image_url)
    except Exception as e:
        print("Describe-image failed:", e)
        raise HTTPException(status_code=503, detail=(
            "المساعد الذكي مش متوصل دلوقتي. اتأكد إن Ollama شغال وإن الموديل بتاع الصور متحمّل." if ar
            else "The AI model isn't reachable. Make sure Ollama is running and a vision model is installed."))

    name = str(result.get("name") or "").strip().strip('"\u201c\u201d').strip()[:150]
    description = str(result.get("description") or "").strip()[:600]
    wanted = {"name": name, "description": description}
    if not (name if body.what != "description" else description) and not (name or description):
        raise HTTPException(status_code=422, detail=(
            "معرفتش أتعرف على منتج في الصورة. جرّب صورة أوضح." if ar
            else "I couldn't recognize an item in this photo. Try a clearer one."))
    return wanted