import { login } from "./actions";
import styles from "./login.module.css";

export default function LoginPage({
  searchParams,
}: {
  searchParams: { error?: string; from?: string };
}) {
  return (
    <div className={styles.page}>
      <form className={styles.card} action={login}>
        <div className={styles.mark} aria-hidden="true">
          <span className={styles.dot} />
        </div>
        <h1 className={styles.title}>Innoventix Console</h1>
        <p className={styles.subtitle}>Enter the dashboard password to continue.</p>

        <input type="hidden" name="from" value={searchParams.from || "/"} />

        <label className={styles.label} htmlFor="password">
          Password
        </label>
        <input
          id="password"
          name="password"
          type="password"
          autoFocus
          required
          className={styles.input}
          placeholder="••••••••"
        />

        {searchParams.error && (
          <p className={styles.error}>That password isn&apos;t right. Try again.</p>
        )}

        <button type="submit" className={styles.button}>
          Enter console
        </button>
      </form>
    </div>
  );
}
