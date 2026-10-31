import styles from "./HourlyChart.module.css";

export default function HourlyChart({
  hours,
  axisLabels,
}: {
  hours: number[];
  axisLabels: string[];
}) {
  // hours[i] = call count in bucket i, oldest to newest
  const max = Math.max(1, ...hours);
  const barWidth = 100 / hours.length;

  return (
    <div className={styles.wrap}>
      <svg viewBox="0 0 100 36" preserveAspectRatio="none" className={styles.svg}>
        {hours.map((count, i) => {
          const height = (count / max) * 30;
          return (
            <rect
              key={i}
              x={i * barWidth + barWidth * 0.15}
              y={34 - height}
              width={barWidth * 0.7}
              height={Math.max(height, count > 0 ? 1.5 : 0.5)}
              rx={0.6}
              className={count > 0 ? styles.bar : styles.barEmpty}
            />
          );
        })}
      </svg>
      <div className={styles.axis}>
        {axisLabels.map((label, i) => (
          <span key={i}>{label}</span>
        ))}
      </div>
    </div>
  );
}
