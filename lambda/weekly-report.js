const costData = require("../data/sample-costs.json");

const formatCurrency = (value) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  }).format(value);

exports.handler = async () => {
  const previous = costData.weekOverWeek.previousWeek;
  const current = costData.weekOverWeek.currentWeek;
  const delta = current - previous;
  const changePercent = (delta / previous) * 100;

  const summary = {
    period: costData.period,
    previousWeek: formatCurrency(previous),
    currentWeek: formatCurrency(current),
    change: formatCurrency(delta),
    changePercent: `${changePercent.toFixed(1)}%`,
  };

  const highlights = costData.services
    .filter((service) => service.cost > 50)
    .map((service) => ({
      service: service.service,
      businessLabel: service.businessLabel,
      cost: formatCurrency(service.cost),
    }));

  return {
    statusCode: 200,
    body: JSON.stringify(
      {
        summary,
        highlights,
        message: "Weekly cost report generated.",
      },
      null,
      2
    ),
  };
};