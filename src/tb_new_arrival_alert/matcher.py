from .models import Item, Target


def matches_target(item: Item, target: Target) -> bool:
    haystack = f"{item.title} {item.source_text}".lower()

    include = [keyword.lower() for keyword in target.include_keywords if keyword]
    if include and not any(keyword in haystack for keyword in include):
        return False

    exclude = [keyword.lower() for keyword in target.exclude_keywords if keyword]
    if exclude and any(keyword in haystack for keyword in exclude):
        return False

    if item.price is not None:
        if target.price_min is not None and item.price < target.price_min:
            return False
        if target.price_max is not None and item.price > target.price_max:
            return False

    return True

