"use client";

import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { ArrowLeft, ArrowRight } from "lucide-react";

interface Props {
  siteId: string;
  step: number;
  onNext?: () => Promise<void> | void;   // validate + persist before navigating
  nextDisabled?: boolean;
  nextLabel?: string;
  saving?: boolean;
}

/**
 * Shared bottom action bar for every wizard step.
 * "Назад" goes to step-1 without saving; "Далее" calls onNext (persist)
 * then advances. Keeps all seven pages from re-implementing navigation.
 */
export function StepNav({ siteId, step, onNext, nextDisabled, nextLabel, saving }: Props) {
  const router = useRouter();

  async function handleNext() {
    if (onNext) await onNext();
    const next = Math.min(step + 1, 7);
    router.push(`/onboarding/${siteId}/step/${next}`);
  }

  return (
    <div className="mt-8 flex items-center justify-between pt-4 border-t">
      <Button
        variant="ghost"
        size="sm"
        onClick={() => router.push(`/onboarding/${siteId}/step/${Math.max(1, step - 1)}`)}
        disabled={step === 1}
      >
        <ArrowLeft className="mr-2 h-4 w-4" /> Назад
      </Button>
      <Button
        onClick={handleNext}
        disabled={nextDisabled || saving}
      >
        {saving ? "Сохраняю…" : (nextLabel ?? "Далее")}
        <ArrowRight className="ml-2 h-4 w-4" />
      </Button>
    </div>
  );
}
