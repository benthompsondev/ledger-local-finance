"""
Merchant categorization rules based on common statement patterns.
Format: (pattern, category, subcategory, is_recurring)
Rules are case-insensitive substring matches. First match wins.
Add your own rules at the bottom.
"""

RULES = [

    # ── SAVINGS-SPECIFIC PATTERNS (must come first — highest priority) ──────────────────
    # Internal savings → chequing transfers — must NOT count as income or spending.
    # Pass 17: routed to "Internal Transfer" so they're clearly distinct from
    # Tangerine-Savings investment movements ("Savings" category).
    ("Internet Withdrawal to Tangerine Chequing", "Internal Transfer", "Savings -> Chequing", False),
    ("Internet Transfer to Tangerine Chequing",   "Internal Transfer", "Savings -> Chequing", False),
    ("Internet Deposit from Tangerine Savings",   "Internal Transfer", "Chequing <- Savings", False),
    ("Internet Deposit from Tangerine",           "Internal Transfer", "Chequing <- Savings", False),

    # Real income into savings account — Pass 17 promoted these to specific
    # income subtypes so the Income page can break them out correctly.
    ("EFT Deposit from MANULIFE",      "Reimbursement / Insurance Reimbursement", "Manulife Health", False),
    ("MANULIFE",                       "Reimbursement / Insurance Reimbursement", "Manulife Health", False),
    ("Credit Card Rewards Redemption", "Rewards / Cashback", "Tangerine MC Rewards", False),
    ("REWARDS REDEMPTION",             "Rewards / Cashback", "Tangerine MC Rewards", False),

    # Interest is its own income subtype.
    ("Interest Paid",   "Interest Income", "Bank Interest", False),
    ("INTEREST EARNED", "Interest Income", "Bank Interest", False),

    # ── CHEQUING-SPECIFIC PATTERNS ───────────────────────────────────────────

    # Cancellations — must come before generic INTERAC rule
    ("Cancelled INTERAC",         "Transfer",             "Cancelled e-Transfer",  False),

    # Opening / Closing Balance lines — skip these
    ("Opening Balance",           None,                   None,                    False),  # None = skip row
    ("Closing Balance",           None,                   None,                    False),

    # Income — Pass 17: promoted to specific subtypes so Income page breaks
    # them out cleanly. SJH = Saint Joseph's Healthcare payroll deposit.
    ("PAYROLL",                   "Payroll Income",       "Payroll",               False),
    ("EFT Deposit",               "Income",               "Direct Deposit",        False),
    ("TAX REFUND",                "Income",               "CRA",                   False),
    ("CRA ",                      "Income",               "Government",            False),
    ("Shakepay",                  "Income",               "Crypto/Shakepay",       False),
    ("DRAFTERS INC",              "Income",               "Freelance",             False),

    # Mortgage
    ("FIRST NATIONAL",            "Housing / Mortgage",   "Mortgage",              True),
    ("MORTGAGE",                  "Housing / Mortgage",   "Mortgage",              True),

    # CC Payments from chequing — exact Tangerine label
    ("Tangerine Credit Card Payment", "Credit Card Payment", "Tangerine MC",       False),
    ("MASTERCARD",                "Credit Card Payment",  "MC Payment",            False),
    ("VISA PAYMENT",              "Credit Card Payment",  "Visa Payment",          False),

    # Generic Internet Deposit fallback — anything we can't trace to
    # Tangerine Savings is treated as a passthrough internal transfer.
    # Specific Tangerine Savings rules above already grabbed those.
    ("INTERNET DEPOSIT",          "Internal Transfer",    "Internal Deposit",      False),

    # ── INTERAC e-Transfers ──────────────────────────────────────────────────
    # IMPORTANT: Update the two "Self-Transfer" rules below to match the exact
    # account holder name as it appears on Tangerine bank statements.
    # These rules must come BEFORE the generic "To:" / "From:" rules.
    # Without them, e-Transfers to your own name will count as spending/income.
    #
    # To update: Settings -> Rules -> find "YOUR NAME" and replace with
    # the exact name shown on your statements (case-insensitive match).
    ("INTERAC e-Transfer To: YOUR NAME",   "Transfer",      "Self-Transfer",         False),
    ("INTERAC e-Transfer From: YOUR NAME", "Transfer",      "Self-Transfer",         False),

    # Outbound to other people = real spending
    ("INTERAC e-Transfer To:",    "Transfer Out",         "e-Transfer Sent",       False),
    # Inbound from other people = real income
    ("INTERAC e-Transfer From:",  "Transfer In",          "e-Transfer Received",   False),
    # Generic INTERAC fallback
    ("INTERAC e-Transfer",        "Transfer",             "Interac e-Transfer",    False),
    ("INTERAC",                   "Transfer",             "Interac",               False),

    # ── MASTERCARD-SPECIFIC PATTERNS ────────────────────────────────────────

    # Mastercard payments received (shows on MC statement, not chequing)
    ("PAYMENT - THANK YOU",       "Credit Card Payment",  "Payment Received",      False),

    # Fees and interest — exact labels from MC statements
    ("CASH INTEREST",             "Fees / Interest",      "Cash Interest",         False),
    ("CASH ADVANCE FEE",          "Fees / Interest",      "Cash Advance Fee",      False),
    ("PAYMENTUS-SERVICE-FEE",     "Fees / Interest",      "Utility Payment Fee",   True),
    ("NSF",                       "Fees / Interest",      "NSF Fee",               False),
    ("SERVICE CHARGE",            "Fees / Interest",      "Bank Fee",              False),
    ("ANNUAL FEE",                "Fees / Interest",      "Annual Fee",            True),
    ("LATE PAYMENT FEE",          "Fees / Interest",      "Late Fee",              False),

    # Cash Advances — show separately
    ("TD BANK",                   "Cash Advance",         "Cash Advance",          False),
    ("CASH ADVANCE",              "Cash Advance",         "Cash Advance",          False),
    ("ATM ADVANCE",               "Cash Advance",         "Cash Advance",          False),

    # ── UTILITIES / BILLS — Pass 17: split out of Housing / Mortgage ─────
    # Energy, internet, mobile, water — recurring household costs that are
    # NOT mortgage. Surfacing these separately in Trends / Spending /
    # Reduce makes the user's actual housing cost legible vs the recurring
    # utility bill stack.
    ("ROGERS",                    "Utilities / Bills",    "Rogers Phone/Internet", True),
    ("BELL ",                     "Utilities / Bills",    "Bell",                  True),
    ("TELUS",                     "Utilities / Bills",    "Telus",                 True),
    ("HYDRO",                     "Utilities / Bills",    "Hydro",                 True),
    ("ENBRIDGE",                  "Utilities / Bills",    "Enbridge Gas",          True),
    ("FIDO",                      "Utilities / Bills",    "Fido",                  True),
    ("KOODO",                     "Utilities / Bills",    "Koodo",                 True),
    ("FREEDOM MOBILE",            "Utilities / Bills",    "Freedom Mobile",        True),
    ("VIRGIN PLUS",               "Utilities / Bills",    "Virgin Plus",           True),
    ("UNION GAS",                 "Utilities / Bills",    "Gas Utility",           True),
    ("HYDRO ONE",                 "Utilities / Bills",    "Hydro One",             True),
    ("ENERCARE",                  "Utilities / Bills",    "Enercare",              True),
    ("REGION OF WATERLOO",        "Utilities / Bills",    "Water/Region",          True),
    ("CITY OF",                   "Utilities / Bills",    "Municipal",             True),

    # ── GROCERIES ────────────────────────────────────────────────────────────
    ("COSTCO WHOLESALE",          "Groceries",            "Costco",                False),
    ("COSTCO",                    "Groceries",            "Costco",                False),
    ("ROSS & LINDSAY",            "Groceries",            "No Frills",             False),
    ("FRESHCO",                   "Groceries",            "FreshCo",               False),
    ("ZEHRS",                     "Groceries",            "Zehrs",                 False),
    ("LOBLAWS",                   "Groceries",            "Loblaws",               False),
    ("SOBEYS",                    "Groceries",            "Sobeys",                False),
    ("METRO ",                    "Groceries",            "Metro",                 False),
    ("FORTINOS",                  "Groceries",            "Fortinos",              False),
    ("NO FRILLS",                 "Groceries",            "No Frills",             False),
    ("FOOD BASICS",               "Groceries",            "Food Basics",           False),
    ("M&M FOOD MARKET",           "Groceries",            "M&M Food",              False),
    ("SUPERSTORE",                "Groceries",            "Superstore",            False),
    ("WALMART",                   "Groceries",            "Walmart",               False),
    ("WAL-MART",                  "Groceries",            "Walmart",               False),
    ("GIANT TIGER",               "Groceries",            "Giant Tiger",           False),
    ("T&T",                       "Groceries",            "T&T Supermarket",       False),
    ("FARM BOY",                  "Groceries",            "Farm Boy",              False),
    ("JAMES STREET MARKET",       "Groceries",            "James Street Market",   False),
    ("BIG BEE FOOD",              "Groceries",            "Big Bee Food Mart",     False),

    # ── FOOD & CONVENIENCE ───────────────────────────────────────────────────
    ("TIM HORTONS",               "Food & Convenience",   "Tim Hortons",           False),
    ("TIMS ",                     "Food & Convenience",   "Tim Hortons",           False),
    ("MCDONALD",                  "Food & Convenience",   "McDonald's",            False),
    ("MCDONALDS",                 "Food & Convenience",   "McDonald's",            False),
    ("KFC",                       "Food & Convenience",   "KFC",                   False),
    ("HARVEYS",                   "Food & Convenience",   "Harvey's",              False),
    ("HARVEY'S",                  "Food & Convenience",   "Harvey's",              False),
    ("WENDY'S",                   "Food & Convenience",   "Wendy's",               False),
    ("WENDYS",                    "Food & Convenience",   "Wendy's",               False),
    ("TACO BELL",                 "Food & Convenience",   "Taco Bell",             False),
    ("SUBWAY",                    "Food & Convenience",   "Subway",                False),
    ("BURGER KING",               "Food & Convenience",   "Burger King",           False),
    ("PIZZA",                     "Food & Convenience",   "Pizza",                 False),
    ("DOORDASH",                  "Food & Convenience",   "DoorDash",              True),
    ("DD/DOORDASH",               "Food & Convenience",   "DoorDash",              True),
    ("UBER EATS",                 "Food & Convenience",   "Uber Eats",             True),
    ("UBEREATS",                  "Food & Convenience",   "Uber Eats",             True),
    ("UBER CANADA/UBEREATS",      "Food & Convenience",   "Uber Eats",             True),
    ("SKIP THE DISHES",           "Food & Convenience",   "SkipTheDishes",         True),
    ("STARBUCKS",                 "Food & Convenience",   "Starbucks",             False),
    ("BARBURRITO",                "Food & Convenience",   "BarBurrito",            False),
    ("WINGMASTER",                "Food & Convenience",   "Wingmaster",            False),
    ("OSMOW",                     "Food & Convenience",   "Osmow's",               False),
    ("TWICE THE DEAL",            "Food & Convenience",   "Twice the Deal Pizza",  False),
    ("TOMMY'S PIZZA",             "Food & Convenience",   "Tommy's Pizza",         False),
    ("SWISS PLUS",                "Food & Convenience",   "Swiss Plus",            False),
    ("TST-",                      "Food & Convenience",   "Restaurant",            False),
    ("RIZZOS",                    "Food & Convenience",   "Rizzo's",               False),
    ("IRON COW",                  "Food & Convenience",   "The Iron Cow",          False),
    ("RESTAURANT",                "Food & Convenience",   "Restaurant",            False),
    ("PLANK ON AUGUSTA",          "Food & Convenience",   "Plank on Augusta",      False),
    ("CHIPOTLE",                  "Food & Convenience",   "Chipotle",              False),
    ("POPEYES",                   "Food & Convenience",   "Popeyes",               False),
    ("DAIRY QUEEN",               "Food & Convenience",   "Dairy Queen",           False),
    ("7-ELEVEN",                  "Food & Convenience",   "7-Eleven",              False),
    ("CIRCLE K",                  "Food & Convenience",   "Circle K",              False),
    ("HOSPITAL CAFETERIA",        "Food & Convenience",   "Cafeteria",             False),
    ("MARBLE SLAB",               "Food & Convenience",   "Marble Slab",           False),
    ("MEDITERRANE",               "Food & Convenience",   "Mediterraneo",          False),

    # ── GAS / TRANSPORT ──────────────────────────────────────────────────────
    ("PETRO-CANADA",              "Gas / Transport",      "Petro-Canada",          False),
    ("SHELL",                     "Gas / Transport",      "Shell",                 False),
    ("ESSO",                      "Gas / Transport",      "Esso",                  False),
    ("PIONEER",                   "Gas / Transport",      "Pioneer Gas",           False),
    ("SUNOCO",                    "Gas / Transport",      "Sunoco",                False),
    ("ULTRAMAR",                  "Gas / Transport",      "Ultramar",              False),
    ("UPPER JAMES ST MOBIL",      "Gas / Transport",      "Mobil",                 False),
    ("HWY 5 TRUCK STOP",          "Gas / Transport",      "Truck Stop",            False),
    ("WOODLAWN ESSO",             "Gas / Transport",      "Esso",                  False),
    ("CAMBRIDGE ESSO",            "Gas / Transport",      "Esso",                  False),
    ("CAMBRIDGE RACE TRAC",       "Gas / Transport",      "Racetrac Gas",          False),
    ("GAS BAR",                   "Gas / Transport",      "Gas",                   False),
    ("GREAT CANADIAN OIL",        "Gas / Transport",      "Oil Change",            False),
    ("UBER ",                     "Gas / Transport",      "Uber",                  False),
    ("LYFT",                      "Gas / Transport",      "Lyft",                  False),
    ("GO TRANSIT",                "Gas / Transport",      "Transit",               True),
    ("OC TRANSPO",                "Gas / Transport",      "Transit",               True),
    ("TTC",                       "Gas / Transport",      "TTC",                   True),
    ("PRESTO",                    "Gas / Transport",      "Presto",                True),
    ("PARKING",                   "Gas / Transport",      "Parking",               False),
    ("CANADA WIDE PARKING",       "Gas / Transport",      "Parking",               False),
    ("TARGET PARK GROUP",         "Gas / Transport",      "Parking",               False),
    ("RMOW ADMINISTRATIVE",       "Gas / Transport",      "Parking/Admin",         False),

    # ── HOME IMPROVEMENT — Pass 17 ─────────────────────────────────────
    # Hardware / DIY / home repair — separated out of Shopping so big
    # one-off project months don't make Shopping look like normal
    # discretionary lifestyle spending.
    ("HOME DEPOT",                "Home Improvement",     "Home Depot",            False),
    ("THE HOME DEPOT",            "Home Improvement",     "Home Depot",            False),
    ("LOWES",                     "Home Improvement",     "Lowe's",                False),
    ("LOWE'S",                    "Home Improvement",     "Lowe's",                False),
    ("RONA",                      "Home Improvement",     "Rona",                  False),
    ("CANADIAN TIRE",             "Home Improvement",     "Canadian Tire",         False),
    ("PRINCESS AUTO",             "Home Improvement",     "Princess Auto",         False),
    ("BUSY BEAVER",               "Home Improvement",     "Busy Beaver",           False),
    ("PARAMOUNT FENCE",           "Home Improvement",     "Fencing",               False),
    ("LEON'S",                    "Home Improvement",     "Leon's Furniture",      False),
    ("BAD BOY",                   "Home Improvement",     "Bad Boy Furniture",     False),
    ("THE BRICK",                 "Home Improvement",     "The Brick",             False),
    ("WAYFAIR",                   "Home Improvement",     "Wayfair",               False),
    ("STRUCTUBE",                 "Home Improvement",     "Structube",             False),
    ("BLINDS TO GO",              "Home Improvement",     "Blinds To Go",          False),

    # ── SHOPPING ─────────────────────────────────────────────────────────────
    ("AMAZON.CA",                 "Shopping",             "Amazon.ca",             False),
    ("AMAZON*",                   "Shopping",             "Amazon",                False),
    ("AMAZON CHANNELS",           "Subscriptions & Digital", "Amazon Channels",   True),
    ("AMZN",                      "Shopping",             "Amazon",                False),
    ("TEMU.COM",                  "Shopping",             "Temu",                  False),
    ("BEST BUY",                  "Shopping",             "Best Buy",              False),
    ("BESTBUY",                   "Shopping",             "Best Buy",              False),
    ("IKEA",                      "Shopping",             "IKEA",                  False),
    ("WINNERS",                   "Shopping",             "Winners",               False),
    ("SPORT CHEK",                "Shopping",             "Sport Chek",            False),
    ("PRO HOCKEY LIFE",           "Shopping",             "Pro Hockey Life",       False),
    ("DOLLARAMA",                 "Shopping",             "Dollarama",             False),
    ("DOLLAR TREE",               "Shopping",             "Dollar Tree",           False),
    ("2NDTURN CANADA",            "Shopping",             "2ndTurn",               False),
    ("AMAZE DEALS",               "Shopping",             "Amaze Deals",           False),
    ("SEPHORA",                   "Shopping",             "Sephora",               False),
    ("BLUENOTES",                 "Shopping",             "Blue Notes",            False),
    ("URBAN PLANET",              "Shopping",             "Urban Planet",          False),
    ("MINISO",                    "Shopping",             "Miniso",                False),
    ("NANONOBLE",                 "Shopping",             "Nanonoble",             False),
    ("GIFTPASS",                  "Shopping",             "Gift Card",             False),
    ("TRUE NORTH CANNABIS",       "Shopping",             "True North Cannabis",   False),
    ("CANADA COMPUTERS",          "Shopping",             "Canada Computers",      False),
    ("CARDS AND CHORDS",          "Shopping",             "Cards and Chords",      False),
    ("GRNHRZNSODB",               "Shopping",             "Green Horizons (landscape)", False),
    ("IMAGINEX",                  "Shopping",             "Imaginex",              False),
    ("CREEM.IO*IMAGINEX",         "Subscriptions & Digital", "Imaginex Sub",       True),
    ("ECONOMICAL INSURANCE",      "Shopping",             "Insurance",             True),
    ("MARSHALL",                  "Shopping",             "Marshalls",             False),
    ("H&M",                       "Shopping",             "H&M",                   False),
    ("ZARA",                      "Shopping",             "Zara",                  False),
    ("EB GAMES",                  "Shopping",             "EB Games",              False),
    ("LM WATERLOO",               "Shopping",             "Local Market",          False),
    ("ART PALACE",                "Shopping",             "Art Palace",            False),

    # ── SUBSCRIPTIONS & DIGITAL ──────────────────────────────────────────────
    ("OPENAI *CHATGPT SUBSCR",    "Subscriptions & Digital", "ChatGPT",            True),
    ("OPENAI* CHATGPT",           "Subscriptions & Digital", "ChatGPT",            True),
    ("OPENAI",                    "Subscriptions & Digital", "OpenAI",             True),
    ("DISNEY PLUS",               "Subscriptions & Digital", "Disney+",            True),
    ("Disney Plus",               "Subscriptions & Digital", "Disney+",            True),
    ("NETFLIX",                   "Subscriptions & Digital", "Netflix",            True),
    ("SPOTIFY",                   "Subscriptions & Digital", "Spotify",            True),
    ("APPLE.COM/BILL",            "Subscriptions & Digital", "Apple",              True),
    ("APPLE.COM",                 "Subscriptions & Digital", "Apple",              True),
    ("GOOGLE",                    "Subscriptions & Digital", "Google",             True),
    ("MICROSOFT",                 "Subscriptions & Digital", "Microsoft",          True),
    ("XBOX",                      "Subscriptions & Digital", "Xbox",               True),
    ("DISCORD",                   "Subscriptions & Digital", "Discord",            True),
    ("YOUTUBE PREMIUM",           "Subscriptions & Digital", "YouTube",            True),
    ("AMAZON PRIME",              "Subscriptions & Digital", "Amazon Prime",       True),
    ("PRIME VIDEO",               "Subscriptions & Digital", "Prime Video",        True),
    ("Patreon",                   "Subscriptions & Digital", "Patreon",            True),
    ("CRAVE",                     "Subscriptions & Digital", "Crave",              True),
    ("PARAMOUNT",                 "Subscriptions & Digital", "Paramount+",         True),
    ("DROPBOX",                   "Subscriptions & Digital", "Dropbox",            True),
    ("GITHUB",                    "Subscriptions & Digital", "GitHub",             True),
    ("NOTION",                    "Subscriptions & Digital", "Notion",             True),
    ("ADOBE",                     "Subscriptions & Digital", "Adobe",              True),
    ("CLAUDE",                    "Subscriptions & Digital", "Claude/Anthropic",   True),
    ("ANTHROPIC",                 "Subscriptions & Digital", "Anthropic",          True),
    ("PERPLEXITY",                "Subscriptions & Digital", "Perplexity",         True),
    ("LEONARDO.AI",               "Subscriptions & Digital", "Leonardo.AI",        True),
    ("PIXVERSE",                  "Subscriptions & Digital", "PixVerse AI",        True),
    ("HAILUOAI",                  "Subscriptions & Digital", "Hailuo AI",          True),
    ("HIX.AI",                    "Subscriptions & Digital", "Hix.AI",             True),
    ("AI STORY GENERATOR",        "Subscriptions & Digital", "AI Story Gen",       True),
    ("X DEVELOPER PLATFORM",      "Subscriptions & Digital", "X/Twitter API",      True),
    ("NDAI.CHAT",                 "Subscriptions & Digital", "NDAI Chat",          True),
    ("DEVIANTART",                "Subscriptions & Digital", "DeviantArt",         True),
    ("XIENX INC",                 "Subscriptions & Digital", "Xienx",              True),
    ("DA*",                       "Subscriptions & Digital", "DeviantArt",         True),
    # ── ENTERTAINMENT — Pass 17 ────────────────────────────────────────
    # Real-world entertainment / one-off games / cinemas. Distinct from
    # Subscriptions & Digital (recurring) so the user can see how much of
    # their money is going to one-off fun vs. monthly bills.
    ("CINEPLEX",                  "Entertainment",        "Cineplex",              False),
    ("LANDMARK CINEMAS",          "Entertainment",        "Landmark Cinemas",      False),
    ("AMC ",                      "Entertainment",        "AMC",                   False),
    ("STEAM",                     "Entertainment",        "Steam",                 False),
    ("STEAMGAMES",                "Entertainment",        "Steam",                 False),
    ("STEAMPOWERED",              "Entertainment",        "Steam",                 False),
    ("PlayStation Network",       "Entertainment",        "PlayStation",           True),
    ("RIOT* ",                    "Entertainment",        "Riot Games",            False),
    ("RIOT GAMES",                "Entertainment",        "Riot Games",            False),
    ("EPIC GAMES",                "Entertainment",        "Epic Games",            False),
    ("EA ",                       "Entertainment",        "EA Games",              False),
    ("G2ABVSHOP",                 "Entertainment",        "G2A Games",             False),
    ("G2A.COM",                   "Entertainment",        "G2A Games",             False),
    ("GAMESTOP",                  "Entertainment",        "GameStop",              False),
    ("CONCERT",                   "Entertainment",        "Concert",               False),
    ("TICKETMASTER",              "Entertainment",        "Ticketmaster",          False),
    ("STUBHUB",                   "Entertainment",        "StubHub",               False),
    ("LIVE NATION",               "Entertainment",        "Live Nation",           False),
    ("BOWLING",                   "Entertainment",        "Bowling",               False),
    ("ESCAPE ROOM",               "Entertainment",        "Escape Room",           False),

    # ── HEALTH / CARE ────────────────────────────────────────────────────────
    ("ST JOSEPH'S HEALTHCARE",    "Health / Care",        "SJH Healthcare",        True),
    ("ABMA COUNSELLING",          "Health / Care",        "Counselling",           True),
    ("WATERLOO FAMILY DENTAL",    "Health / Care",        "Dental",                False),
    ("DENTAL",                    "Health / Care",        "Dental",                False),
    ("DENTIST",                   "Health / Care",        "Dental",                False),
    ("PHARMACY",                  "Health / Care",        "Pharmacy",              False),
    ("REXALL",                    "Health / Care",        "Pharmacy",              False),
    ("SHOPPERS DRUG",             "Health / Care",        "Pharmacy",              False),
    ("MEDICAL",                   "Health / Care",        "Medical",               False),
    ("DOCTOR",                    "Health / Care",        "Doctor",                False),
    ("OPTOMETRIST",               "Health / Care",        "Vision",                False),
    ("GYM",                       "Health / Care",        "Gym",                   True),
    ("GOODLIFE",                  "Health / Care",        "GoodLife Fitness",      True),
    ("YMCA",                      "Health / Care",        "YMCA",                  True),

    # ── PETS ─────────────────────────────────────────────────────────────────
    ("PETSMART",                  "Pets",                 "PetSmart",              False),
    ("PET VALU",                  "Pets",                 "Pet Valu",              False),
    ("PETCO",                     "Pets",                 "Petco",                 False),
    ("GLOBAL PET",                "Pets",                 "Global Pet",            False),
    ("VETERINARY",                "Pets",                 "Vet",                   False),
    ("VETERINAIRE",               "Pets",                 "Vet",                   False),
    ("VET ",                      "Pets",                 "Vet",                   False),

    # ── INVESTMENTS ───────────────────────────────────────────────────────────
    ("WEALTHSIMPLE",              "Investments",          "Wealthsimple",          True),
    ("QUESTRADE",                 "Investments",          "Questrade",             True),
    ("TANGERINE INV",             "Investments",          "Tangerine Invest",      True),
    ("RRSP",                      "Investments",          "RRSP",                  True),
    ("TFSA",                      "Investments",          "TFSA",                  True),
    ("FHSA",                      "Investments",          "FHSA",                  True),

]
