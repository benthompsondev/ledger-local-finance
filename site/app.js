const tours = {
  home: {
    kicker: "A useful weekly check-in",
    title: "Start with what changed, not a wall of charts",
    copy: "Home shows money in, spending, what you kept, same-day comparisons, and the next decisions that can improve the numbers.",
    points: [
      "Equal-period spending comparisons",
      "One balance-checked Safe to Spend number",
      "Plain-language findings from local calculations",
    ],
    image: "assets/home.png",
    alt: "SignalSpace Home screen using synthetic data",
  },
  profiles: {
    kicker: "One installation, separate finances",
    title: "Two people in a household never see each other's money",
    copy: "Each profile keeps its own transactions, accounts, imports, plans and net worth in its own database. Switching profiles opens a different database rather than filtering a shared one, so a profile you are not in is not open at all.",
    points: [
      "A separate SQLite database per profile",
      "Imports, backups and exports scoped to the open profile",
      "Create, rename, switch and safely delete from Settings",
    ],
    image: "assets/profiles.png",
    alt: "SignalSpace profile management showing two separate sets of finances, using synthetic data",
  },
  plan: {
    kicker: "A plan that shows its work",
    title: "See what is committed before deciding what is flexible",
    copy: "Plan separates income, fixed costs, slower bills, savings, and a safety buffer. Missing or overdue data lowers confidence instead of disappearing.",
    points: [
      "Monthly, quarterly, and annual commitments",
      "Received income kept separate from expected income",
      "A review step before the plan becomes authoritative",
    ],
    image: "assets/plan.png",
    alt: "SignalSpace Plan screen using synthetic data",
  },
  insights: {
    kicker: "Patterns that keep the comparison fair",
    title: "Compare the same days and separate choices from fixed bills",
    copy: "Insights tracks flexible pace, categories, income steadiness, merchants, recurring costs, and recorded net worth without mixing unlike periods.",
    points: [
      "Same-day spending pace and category movement",
      "Spending heatmap and weekday patterns",
      "Income, recurring costs, and recorded net-worth trend",
    ],
    image: "assets/insights.png",
    alt: "SignalSpace Insights screen using synthetic data",
  },
};

const tabs = [...document.querySelectorAll("[data-tour]")];
const kicker = document.querySelector("#tour-kicker");
const title = document.querySelector("#tour-title");
const copy = document.querySelector("#tour-copy");
const points = document.querySelector("#tour-points");
const image = document.querySelector("#tour-image");

function selectTour(name) {
  const tour = tours[name];
  if (!tour) return;

  tabs.forEach((tab) => {
    const selected = tab.dataset.tour === name;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
  document.querySelector("#tour-panel").setAttribute("aria-labelledby", `tour-${name}`);
  kicker.textContent = tour.kicker;
  title.textContent = tour.title;
  copy.textContent = tour.copy;
  points.replaceChildren(...tour.points.map((point) => {
    const item = document.createElement("li");
    item.textContent = point;
    return item;
  }));
  image.src = tour.image;
  image.alt = tour.alt;
}

tabs.forEach((tab, index) => {
  tab.addEventListener("click", () => selectTour(tab.dataset.tour));
  tab.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    event.preventDefault();
    const next = event.key === "ArrowRight"
      ? (index + 1) % tabs.length
      : (index - 1 + tabs.length) % tabs.length;
    tabs[next].focus();
    selectTour(tabs[next].dataset.tour);
  });
});
