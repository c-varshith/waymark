import { Logo } from './Logo'

export function Footer() {
  return (
    <footer className="mt-auto bg-navy text-ivory/90">
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-4 py-6 md:flex-row md:items-center md:justify-between">
        <Logo tone="dark" size={28} />
        <div className="max-w-2xl text-sm leading-relaxed md:text-right">
          <p className="font-semibold text-ivory">
            WAYMARK is decision support only. Human experts decide. Indicative result: one city, one backtest.
          </p>
          <p className="mt-1 text-ivory/70">
            Data: US Accidents (2016 to 2023), a countrywide traffic accident dataset by Sobhan Moosavi et al.
          </p>
        </div>
      </div>
    </footer>
  )
}
