def auto_assign_priority(train_name: str) -> str:
    """
    Automatically determines a train's priority based on its name/category,
    following Indian Railways' real precedence order:
    Vande Bharat / Rajdhani / Tejas / Duronto / Shatabdi / Gatimaan  -> High
    Garib Rath / Jan Shatabdi / Superfast / Express                  -> Medium
    Passenger / Local / MEMU / DEMU                                  -> Low
    """
    name = train_name.lower()

    high_priority_keywords = [
        "vande bharat", "rajdhani", "tejas", "duronto", "shatabdi", "gatimaan"
    ]
    low_priority_keywords = [
        "passenger", "local", "memu", "demu", "shuttle"
    ]

    for keyword in high_priority_keywords:
        if keyword in name:
            return "High"

    for keyword in low_priority_keywords:
        if keyword in name:
            return "Low"

    # anything else (Superfast, Mail, Express, Garib Rath, Jan Shatabdi etc.)
    return "Medium"