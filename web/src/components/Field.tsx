import type { InputHTMLAttributes, TextareaHTMLAttributes } from "react";

type Common = { label: string; hideLabel?: boolean };
type InputFieldProps = Common & { multiline?: false } & InputHTMLAttributes<HTMLInputElement>;
type TextareaFieldProps = Common & { multiline: true } & TextareaHTMLAttributes<HTMLTextAreaElement>;

/** Label + control. The label stays in the accessibility tree even when visually hidden. */
export function Field(props: InputFieldProps | TextareaFieldProps) {
  const { label, hideLabel } = props;
  const labelText = <span className={hideLabel ? "sr-only" : "field__label"}>{label}</span>;

  if (props.multiline) {
    const { label: _l, hideLabel: _h, multiline: _m, ...rest } = props;
    return (
      <label className="field">
        {labelText}
        <textarea className="field__control" {...rest} />
      </label>
    );
  }
  const { label: _l, hideLabel: _h, multiline: _m, ...rest } = props;
  return (
    <label className="field">
      {labelText}
      <input className="field__control" {...rest} />
    </label>
  );
}
