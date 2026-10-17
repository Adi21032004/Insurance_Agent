import type { ClaimDetail } from "../api";
import { ClaimOverview } from "./ClaimOverview";
import {
  ActivityCard,
  CustomerCard,
  DecisionCard,
  EvidenceCard,
  PolicyCard,
} from "./ClaimSections";

type ClaimWorkspaceProps = Readonly<{
  detail: ClaimDetail;
  actionBusy: boolean;
  selectedHandler: string;
  onHandlerChange: (handlerId: string) => void;
  onRun: () => void;
  onSignOff: () => void;
  onPay: () => void;
}>;

export function ClaimWorkspace(props: ClaimWorkspaceProps) {
  return (
    <div className="claim-workspace">
      <ClaimOverview {...props} />
      <div className="content-grid">
        <div className="column-stack">
          <EvidenceCard detail={props.detail} />
          <PolicyCard detail={props.detail} />
        </div>
        <div className="column-stack">
          <DecisionCard detail={props.detail} />
          <ActivityCard detail={props.detail} />
          <CustomerCard detail={props.detail} />
        </div>
      </div>
    </div>
  );
}
