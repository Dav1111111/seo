"use client";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center h-[50vh] gap-4">
      <h2 className="text-xl font-semibold">Что-то пошло не так</h2>
      <p className="text-sm text-muted-foreground max-w-md text-center">
        {error.message || "Произошла ошибка при загрузке страницы."}
      </p>
      <button
        onClick={reset}
        className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm"
      >
        Попробовать снова
      </button>
    </div>
  );
}
